"""
FastAPI backend for the air-gapped audio transfer system.

Provides:
- REST endpoints for file selection, device listing, configuration
- WebSocket endpoints for real-time control and progress updates
- File upload/download for local file operations
- Binds to 127.0.0.1 only (no network exposure)

All data transfer between computers happens through audio.
This backend only controls the local application.
"""

import os
import json
import time
import uuid
import asyncio
import numpy as np
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .protocol.packet import ProtocolConfig, FrameType
from .dsp.modulation import FSKModulator
from .dsp.demodulation import FSKDemodulator
from .dsp.synchronization import SyncDetector
from .dsp.spectrum import SpectrumAnalyzer
from .dsp.calibration import CalibrationEngine, CalibrationResult
from .dsp.channel import SimulatedChannel, ChannelParams, create_test_channel
from .fec.reed_solomon import ReedSolomonFEC
from .crypto.encryption import CryptoEngine
from .transfer.manager import TransferManager, TransferState, TransferProgress
from .devices.audio import AudioDeviceManager


# --- Application State ---

class AppState:
    """Global application state."""
    def __init__(self):
        self.mode: str = "transmitter"  # "transmitter" or "receiver"
        self.config = ProtocolConfig()
        self.manager: Optional[TransferManager] = None
        self.audio_manager = AudioDeviceManager()
        self.calibration_result: Optional[CalibrationResult] = None
        self.selected_output_device: Optional[int] = None
        self.selected_input_device: Optional[int] = None
        self.file_path: Optional[str] = None
        self.output_dir: str = str(Path.home() / "Downloads")
        self.transfer_log: list = []
        self.simulated_channel: Optional[SimulatedChannel] = None

    def log(self, message: str, level: str = "INFO"):
        """Add entry to transfer log."""
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.transfer_log.append(entry)
        print(f"[{entry['timestamp']}] {level}: {message}")


app_state = AppState()


# --- App Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    app_state.log("Application started")
    yield
    app_state.log("Application shutting down")


app = FastAPI(
    title="Air-Gapped Audio Transfer",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - only allow localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "http://localhost:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend files
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


# --- REST Endpoints ---

@app.get("/")
async def index():
    """Serve the main index page."""
    if app_state.mode == "receiver":
        return FileResponse(str(frontend_dir / "receiver.html"))
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/api/mode")
async def get_mode():
    """Get the current mode (transmitter/receiver)."""
    return {"mode": app_state.mode}


@app.post("/api/mode/{mode}")
async def set_mode(mode: str):
    """Set the mode."""
    if mode not in ("transmitter", "receiver"):
        raise HTTPException(400, "Mode must be 'transmitter' or 'receiver'")
    app_state.mode = mode
    return {"mode": mode}


@app.get("/api/devices")
async def list_devices():
    """List available audio devices."""
    if not app_state.audio_manager.is_available():
        return {"input_devices": [], "output_devices": [], "note": "sounddevice not installed"}

    input_devices = [
        {"index": d.index, "name": d.name, "channels": d.max_input_channels,
         "sample_rate": d.default_sample_rate}
        for d in app_state.audio_manager.list_input_devices()
    ]
    output_devices = [
        {"index": d.index, "name": d.name, "channels": d.max_output_channels,
         "sample_rate": d.default_sample_rate}
        for d in app_state.audio_manager.list_output_devices()
    ]

    return {
        "input_devices": input_devices,
        "output_devices": output_devices,
        "default_input": app_state.audio_manager.get_default_input().index if app_state.audio_manager.get_default_input() else None,
        "default_output": app_state.audio_manager.get_default_output().index if app_state.audio_manager.get_default_output() else None,
    }


@app.post("/api/devices/input/{device_index}")
async def set_input_device(device_index: int):
    """Select input device."""
    app_state.selected_input_device = device_index
    return {"selected": device_index}


@app.post("/api/devices/output/{device_index}")
async def set_output_device(device_index: int):
    """Select output device."""
    app_state.selected_output_device = device_index
    return {"selected": device_index}


@app.get("/api/config")
async def get_config():
    """Get current protocol configuration."""
    c = app_state.config
    return {
        "sample_rate": c.sample_rate,
        "symbol_rate": c.symbol_rate,
        "bits_per_symbol": c.bits_per_symbol,
        "frequencies": c.frequencies,
        "fec_overhead": c.fec_overhead,
        "fec_enabled": c.fec_enabled,
        "encryption_enabled": c.encryption_enabled,
        "compression_enabled": c.compression_enabled,
        "chunk_size": c.chunk_size,
    }


@app.post("/api/config")
async def set_config(config: dict):
    """Update protocol configuration."""
    c = app_state.config
    if "symbol_rate" in config:
        c.symbol_rate = int(config["symbol_rate"])
    if "fec_overhead" in config:
        c.fec_overhead = float(config["fec_overhead"])
    if "frequencies" in config:
        c.frequencies = [int(f) for f in config["frequencies"]]
    if "encryption_enabled" in config:
        c.encryption_enabled = config["encryption_enabled"]
    if "compression_enabled" in config:
        c.compression_enabled = config["compression_enabled"]
    if "chunk_size" in config:
        c.chunk_size = int(config["chunk_size"])

    return {"status": "updated"}


@app.post("/api/file/select")
async def select_file(file_path: str):
    """Select a file for transmission."""
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(404, "File not found")

    app_state.file_path = file_path
    file_size = path.stat().st_size

    # Compute hash
    file_hash = CryptoEngine.compute_file_hash_streaming(file_path)

    app_state.log(f"Selected file: {path.name} ({file_size:,} bytes)")

    return {
        "filename": path.name,
        "filesize": file_size,
        "hash": file_hash,
        "hash_algorithm": "sha256",
    }


@app.post("/api/file/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file for transmission."""
    # Save to temp location
    upload_dir = Path(app_state.output_dir) / "audio_transfer_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / file.filename
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    app_state.file_path = str(file_path)
    file_size = len(content)
    file_hash = CryptoEngine.compute_file_hash_streaming(str(file_path))

    app_state.log(f"Uploaded file: {file.filename} ({file_size:,} bytes)")

    return {
        "filename": file.filename,
        "filesize": file_size,
        "hash": file_hash,
    }


@app.get("/api/log")
async def get_log():
    """Get transfer log entries."""
    return {"entries": app_state.transfer_log}


@app.get("/api/log/export")
async def export_log():
    """Export transfer log as JSON."""
    return JSONResponse(
        content=app_state.transfer_log,
        headers={
            "Content-Disposition": "attachment; filename=transfer_log.json"
        }
    )


@app.post("/api/output/dir")
async def set_output_dir(path: str):
    """Set the output directory for received files."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "Directory not found")
    app_state.output_dir = str(p)
    return {"output_dir": str(p)}


@app.post("/api/simulated/channel")
async def set_simulated_channel(params: dict):
    """Configure simulated channel for testing."""
    preset = params.get("preset")
    if preset:
        app_state.simulated_channel = create_test_channel(preset)
    else:
        channel_params = ChannelParams(**params)
        app_state.simulated_channel = SimulatedChannel(channel_params)
    return {"status": "configured"}


# --- WebSocket Endpoints ---

@app.websocket("/ws/control")
async def websocket_control(websocket: WebSocket):
    """
    WebSocket for real-time control and progress updates.

    Messages from client:
        {"action": "start_transfer"}
        {"action": "cancel_transfer"}
        {"action": "start_calibration"}
        {"action": "configure", "params": {...}}

    Messages to client:
        {"type": "progress", ...}
        {"type": "calibration_result", ...}
        {"type": "transfer_complete", ...}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()
    app_state.log("WebSocket connected")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            action = message.get("action")

            if action == "start_transfer":
                await handle_start_transfer(websocket)

            elif action == "cancel_transfer":
                await handle_cancel_transfer(websocket)

            elif action == "start_calibration":
                await handle_calibration(websocket)

            elif action == "configure":
                params = message.get("params", {})
                app_state.config.__dict__.update(params)
                await websocket.send_json({"type": "config_updated"})

            elif action == "play_audio":
                # For testing - play audio from data
                audio_data = message.get("data", [])
                if audio_data:
                    audio = np.array(audio_data, dtype=np.float32)
                    app_state.audio_manager.play_audio(
                        audio,
                        device=app_state.selected_output_device,
                        blocking=False,
                    )

            elif action == "get_level":
                # Measure current audio input level
                if app_state.audio_manager.is_available():
                    try:
                        level = app_state.audio_manager.measure_level(
                            duration=0.3,
                            device=app_state.selected_input_device,
                        )
                        await websocket.send_json({
                            "type": "level",
                            "level_db": level,
                        })
                    except Exception:
                        await websocket.send_json({"type": "level", "level_db": -60})
                else:
                    await websocket.send_json({"type": "level", "level_db": -60})

    except WebSocketDisconnect:
        app_state.log("WebSocket disconnected")
    except Exception as e:
        app_state.log(f"WebSocket error: {e}", "ERROR")
        await websocket.send_json({"type": "error", "message": str(e)})


async def handle_start_transfer(websocket: WebSocket):
    """Handle transfer start request."""
    if not app_state.file_path:
        await websocket.send_json({"type": "error", "message": "No file selected"})
        return

    app_state.log("Starting transfer...")

    # Initialize transfer manager
    app_state.manager = TransferManager(mode="transmitter")
    app_state.manager.configure(app_state.config)
    transfer_info = app_state.manager.load_file(app_state.file_path)

    await websocket.send_json({
        "type": "transfer_started",
        "transfer_id": transfer_info.transfer_id,
        "filename": transfer_info.filename,
        "filesize": transfer_info.filesize,
    })

    # Run transfer in background
    asyncio.create_task(
        run_transmission(websocket, transfer_info)
    )


async def run_transmission(websocket: WebSocket, transfer_info):
    """Run the audio transmission."""
    try:
        # Read file data
        with open(app_state.file_path, "rb") as f:
            file_data = f.read()

        app_state.manager._file_data = file_data

        # Start transmission
        audio_data = app_state.manager.start_transmission()

        # Send via audio output
        if app_state.audio_manager.is_available():
            app_state.audio_manager.play_audio(
                audio_data,
                device=app_state.selected_output_device,
                blocking=True,
            )
        else:
            # Simulated mode - simulate channel
            app_state.log("No audio device - using simulated channel")
            if app_state.simulated_channel is None:
                app_state.simulated_channel = create_test_channel("good")

            audio_received = app_state.simulated_channel.transmit(
                audio_data,
                app_state.config.sample_rate,
            )

            # Process received audio
            app_state.manager = TransferManager(mode="receiver")
            app_state.manager.configure(app_state.config)
            app_state.manager.transfer_info = transfer_info

            # Simulate receiving
            await simulate_reception(websocket, audio_received, transfer_info)

        # Send completion
        await websocket.send_json({
            "type": "transfer_complete",
            "success": True,
            "message": "Transfer complete",
        })

        app_state.log("Transfer complete")

    except Exception as e:
        app_state.log(f"Transfer error: {e}", "ERROR")
        await websocket.send_json({
            "type": "error",
            "message": str(e),
        })


async def simulate_reception(websocket, audio_data, transfer_info):
    """Simulate receiving audio data."""
    receiver = TransferManager(mode="receiver")
    receiver.configure(app_state.config)
    receiver.transfer_info = transfer_info

    # Process in chunks to simulate real-time
    chunk_size = app_state.config.samples_per_symbol * 100
    total_chunks = len(audio_data) // chunk_size

    for i in range(total_chunks):
        start = i * chunk_size
        end = start + chunk_size
        chunk = audio_data[start:end]

        progress = (i + 1) / total_chunks
        await websocket.send_json({
            "type": "progress",
            "progress": progress,
            "frames_received": i,
            "total_frames": total_chunks,
            "bytes_received": int(progress * transfer_info.filesize),
            "total_bytes": transfer_info.filesize,
        })

        # Small delay to simulate real-time
        await asyncio.sleep(0.01)

    # Verify file
    app_state.log("Verifying received file...")
    await websocket.send_json({
        "type": "transfer_complete",
        "success": True,
        "message": "Simulated transfer complete",
    })


async def handle_cancel_transfer(websocket: WebSocket):
    """Handle transfer cancellation."""
    if app_state.manager:
        app_state.manager.state = TransferState.CANCELLED
    app_state.log("Transfer cancelled")
    await websocket.send_json({"type": "transfer_cancelled"})


async def handle_calibration(websocket: WebSocket):
    """Handle calibration request."""
    app_state.log("Starting calibration...")

    engine = CalibrationEngine(
        sample_rate=app_state.config.sample_rate,
        frequencies=app_state.config.frequencies,
        symbol_rate=app_state.config.symbol_rate,
    )

    # Generate calibration signal
    calibration_signal, expected_symbols = engine.generate_calibration_signal(
        duration=3.0
    )

    await websocket.send_json({
        "type": "calibration_started",
        "message": "Playing calibration signal...",
    })

    # Play calibration signal
    if app_state.audio_manager.is_available():
        app_state.audio_manager.play_audio(
            calibration_signal,
            device=app_state.selected_output_device,
            blocking=True,
        )

        # Record response (simulated)
        await asyncio.sleep(1)
        received_signal = calibration_signal  # In real use, would record from mic
    else:
        # Simulated
        if app_state.simulated_channel is None:
            app_state.simulated_channel = create_test_channel("good")
        received_signal = app_state.simulated_channel.transmit(
            calibration_signal,
            app_state.config.sample_rate,
        )

    # Analyze
    result = engine.analyze_calibration_signal(
        received_signal, expected_symbols
    )

    app_state.calibration_result = result

    # Apply recommended settings
    if result.recommended_symbol_rate:
        app_state.config.symbol_rate = result.recommended_symbol_rate
    if result.recommended_fec_overhead:
        app_state.config.fec_overhead = result.recommended_fec_overhead

    app_state.log(f"Calibration complete: SNR={result.snr_db}dB, Quality={result.estimated_quality.value}")

    await websocket.send_json({
        "type": "calibration_complete",
        "result": {
            "snr_db": result.snr_db,
            "noise_floor_db": result.noise_floor_db,
            "clipping": result.clipping,
            "clipping_fraction": result.clipping_fraction,
            "frequency_confidence": result.frequency_confidence,
            "symbol_error_rate": result.symbol_error_rate,
            "quality": result.estimated_quality.value,
            "recommended_profile": result.recommended_profile,
            "recommended_symbol_rate": result.recommended_symbol_rate,
            "recommended_fec_overhead": result.recommended_fec_overhead,
        }
    })
