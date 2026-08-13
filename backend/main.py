"""Local control plane.

Run one process per computer. Binding is intentionally performed by ``run.py``
at 127.0.0.1; audio frames never pass through FastAPI or its WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.audio_io import MicrophoneFrameCapture, list_devices, play_calibration, play_frames, record_calibration
from backend.dsp.calibration import CalibrationReport, analyze_signal
from backend.dsp.modulation import ModemConfig
from backend.transfer.session import StreamingReceiver, TransferSettings, prepare_transfer

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
RUNTIME = ROOT / ".runtime"


class AppState:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            "mode": mode,
            "status": "WAITING" if mode == "receiver" else "READY",
            "message": "Listening for audio" if mode == "receiver" else "Select a file to begin",
            "progress": 0.0,
            "frames_done": 0,
            "frames_total": 0,
            "signal": 0,
            "transfer_id": None,
            "metadata": None,
            "frames_recovered": 0,
            "frames_corrupted": 0,
            "log": [],
        }
        self.file_path: Path | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.receiver: StreamingReceiver | None = None
        self.capture: MicrophoneFrameCapture | None = None
        self.seen_sequences: set[int] = set()

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.data["log"].append(f"{stamp}  {message}")
            self.data["log"] = self.data["log"][-200:]
            self.data["message"] = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            value = dict(self.data)
            value["log"] = list(self.data["log"])
            return value


def create_app(mode: str = "transmitter") -> FastAPI:
    if mode not in {"transmitter", "receiver"}:
        raise ValueError("mode must be transmitter or receiver")
    app = FastAPI(title=f"Airgap Audio Transfer — {mode.title()}", docs_url=None, redoc_url=None)
    state = AppState(mode)
    app.state.transfer = state
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return state.snapshot()

    @app.get("/api/devices")
    async def devices() -> list[dict[str, Any]]:
        return [{"index": device.index, "name": device.name, "max_input_channels": device.max_input_channels, "max_output_channels": device.max_output_channels, "default_sample_rate": device.default_sample_rate} for device in list_devices()]

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        if mode != "transmitter":
            raise HTTPException(400, "file upload is only available in transmitter mode")
        RUNTIME.mkdir(exist_ok=True)
        name = Path(file.filename or "upload.bin").name
        destination = RUNTIME / f"{secrets.token_hex(8)}-{name}"
        with destination.open("wb") as output:
            while block := await file.read(1024 * 1024):
                output.write(block)
        state.file_path = destination
        state.data["file"] = {"name": name, "size": destination.stat().st_size}
        state.log(f"File selected: {name}")
        return state.snapshot()

    @app.post("/api/clear-file")
    async def clear_file() -> dict[str, Any]:
        if mode != "transmitter":
            raise HTTPException(400, "file selection is only available in transmitter mode")
        state.file_path = None
        state.data.pop("file", None)
        state.log("File selection cleared")
        return state.snapshot()

    @app.post("/api/calibrate")
    async def calibrate(options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        device = int(options["device"]) if options.get("device") not in (None, "") else None
        try:
            if mode == "transmitter":
                play_calibration(ModemConfig(), device=device)
                report = analyze_signal(__import__("numpy").zeros(1), ModemConfig())
            else:
                report = analyze_signal(record_calibration(ModemConfig(), device=device), ModemConfig())
        except Exception as exc:
            raise HTTPException(503, f"calibration audio device unavailable: {exc}") from exc
        return {"signal_detected": report.signal_detected, "snr_db": report.snr_db, "noise_floor_db": report.noise_floor_db, "clipping": report.clipping, "frequency_confidence": report.frequency_confidence, "microphone_level": report.microphone_level, "reliability": report.reliability, "recommended_profile": report.recommended_profile}

    @app.post("/api/start")
    async def start(options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        if state.thread and state.thread.is_alive():
            raise HTTPException(409, "a transfer is already running")
        state.stop_event.clear()
        if mode == "transmitter":
            if state.file_path is None:
                raise HTTPException(400, "select a file first")
            encryption = bool(options.get("encryption", True))
            password = str(options["password"]) if encryption and options.get("password") else None
            if encryption and not password:
                raise HTTPException(400, "enter the out-of-band password or disable encryption")
            settings = TransferSettings(
                chunk_size=int(options.get("chunk_size", 16 * 1024)),
                fec_overhead=int(options.get("fec_overhead", 25)),
                compression=str(options.get("compression", "zstd")),
            )
            prepared = prepare_transfer(state.file_path, settings, password=password)
            state.data.update({"status": "TRANSMITTING", "progress": 0.0, "frames_done": 0, "frames_total": prepared.total_frames, "transfer_id": f"{prepared.transfer_id:016X}", "metadata": prepared.metadata, "frames_recovered": 0, "frames_corrupted": 0})
            state.log("Transfer initialized; audio is the data plane")

            def run_sender() -> None:
                try:
                    device = int(options["device"]) if options.get("device") not in (None, "") else None
                    play_frames(prepared.frames(), ModemConfig(symbol_rate=int(options.get("symbol_rate", 300))), device=device, on_frame=on_frame, stop_event=state.stop_event)
                    state.data["status"] = "COMPLETE" if not state.stop_event.is_set() else "STOPPED"
                    state.log("Transmission complete")
                except Exception as exc:
                    state.data["status"] = "ERROR"
                    state.log(f"Audio output error: {exc}")

            def on_frame(done: int) -> None:
                state.data["frames_done"] = done
                state.data["progress"] = round(done / prepared.total_frames * 100, 2)

            state.thread = threading.Thread(target=run_sender, name="audio-transmitter", daemon=True)
            state.thread.start()
        else:
            output_dir = Path(str(options.get("output_dir", str(RUNTIME / "received"))))
            password = str(options["password"]) if options.get("password") else None
            state.receiver = StreamingReceiver(output_dir, password=password)
            state.seen_sequences.clear()
            state.data.update({"status": "LISTENING", "progress": 0.0, "frames_done": 0, "frames_total": 0})
            state.log("Receiver listening; only microphone input is used")

            def on_audio(result) -> None:
                for frame in result.frames:
                    if frame.sequence in state.seen_sequences:
                        continue
                    state.seen_sequences.add(frame.sequence)
                    try:
                        state.receiver.accept(frame)
                        state.data["frames_done"] += 1
                        if state.receiver.metadata:
                            state.data["metadata"] = state.receiver.metadata
                            state.data["transfer_id"] = f"{frame.transfer_id:016X}"
                            state.data["frames_total"] = frame.total_frames
                            state.data["progress"] = round(state.data["frames_done"] / max(1, frame.total_frames) * 100, 2)
                        if frame.frame_type.name == "END":
                            finished = state.receiver.finalize()
                            state.data.update({"status": "COMPLETE", "progress": 100.0, "frames_recovered": finished.frames_recovered, "frames_corrupted": finished.frames_corrupted})
                            state.log("File hash verified; transfer successful")
                    except Exception as exc:
                        state.data["status"] = "ERROR"
                        state.log(f"Receiver verification error: {exc}")

            def run_receiver() -> None:
                try:
                    device = int(options["device"]) if options.get("device") not in (None, "") else None
                    state.capture = MicrophoneFrameCapture(ModemConfig(), on_audio, device=device)
                    state.capture.start()
                    while not state.stop_event.wait(0.5):
                        if state.data["status"] in {"COMPLETE", "ERROR", "STOPPED"}:
                            break
                except Exception as exc:
                    state.data["status"] = "ERROR"
                    state.log(f"Audio input error: {exc}")
                finally:
                    if state.capture:
                        state.capture.stop()

            state.thread = threading.Thread(target=run_receiver, name="audio-receiver-control", daemon=True)
            state.thread.start()
        return state.snapshot()

    @app.post("/api/stop")
    async def stop() -> dict[str, Any]:
        state.stop_event.set()
        if state.capture:
            state.capture.stop()
        if state.data["status"] not in {"COMPLETE", "ERROR"}:
            state.data["status"] = "STOPPED"
        state.log("Transfer stopped")
        return state.snapshot()

    @app.get("/api/log")
    async def export_log() -> dict[str, Any]:
        return {"mode": mode, "entries": state.snapshot()["log"]}

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(state.snapshot())
                await asyncio.sleep(0.5)
        except Exception:
            return

    return app
