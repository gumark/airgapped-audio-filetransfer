# Air-Gapped Audio File Transfer System

Transfer files between physically isolated computers using **only sound**. No network, no cables, no Bluetooth — just speakers and microphones.

## 🔒 Security

- **100% Air-Gapped**: The only data channel is speaker → air → microphone
- **No Network Exposure**: Backend binds to `127.0.0.1` only
- **End-to-End Encryption**: Optional ChaCha20-Poly1305 or AES-256-GCM
- **Zero Telemetry**: No uploads, no external APIs, no cloud services
- **Keys Never Transmitted**: Passwords/keys stay on each machine

## 🚀 Quick Start — Vercel (No Install Required)

The fastest way to try the app — works entirely in your browser.

### Deploy

1. Go to [vercel.com/new](https://vercel.com/new)
2. Import the GitHub repo: `gumark/airgapped-audio-filetransfer`
3. Click **Deploy**

Or drag-and-drop the `frontend/` folder at [vercel.com/drop](https://vercel.com/drop).

### Usage

1. Open the deployed URL on **two different computers**
2. On Computer A: Click **Transmitter** → select a file → click **Start Transmission**
3. On Computer B: Click **Receiver** → click **Start Listening**
4. Position the speaker near the microphone
5. Wait for transfer to complete — the file downloads automatically

> **Note**: The browser version uses Web Audio API for audio I/O. Works in Chrome, Edge, Firefox, and Safari.

## 🏗️ Architecture

```
TRANSMITTER                              RECEIVER
    │                                        │
    ▼                                        ▼
┌─────────┐                          ┌─────────────┐
│  File   │                          │  Microphone │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐                          ┌─────────────┐
│ Chunking│                          │Demodulation │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐                          ┌─────────────┐
│FEC      │                          │FEC Decode   │
│Encode   │                          │             │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐                          ┌─────────────┐
│Modulate │                          │Reassemble   │
│(4-FSK)  │                          │& Verify     │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐       ))) AIR (((       ┌─────────────┐
│Speaker  │ ─────────────────────── │Microphone   │
└─────────┘                          └─────────────┘
```

## 📦 Installation — Python Backend (Full Features)

For the complete experience with encryption, calibration, and device selection.

### Prerequisites

- Python 3.10+
- pip
- Audio devices (speakers and microphone)

### Setup

```bash
# Clone the repository
git clone https://github.com/gumark/airgapped-audio-filetransfer.git
cd airgapped-audio-filetransfer

# Install dependencies
pip install -r requirements.txt
```

### Run

```bash
# On Computer A (Transmitter)
python run.py --mode transmitter

# On Computer B (Receiver)
python run.py --mode receiver
```

### Command Line Options

```
python run.py --help

Options:
  --mode {transmitter,receiver}  Run as transmitter or receiver (required)
  --host HOST                    Host to bind to (default: 127.0.0.1)
  --port PORT                    Port to listen on (default: 8000)
  --no-browser                   Don't automatically open browser
```

## 📡 Transfer Procedure

### Step 1: Setup

1. Install the application on both computers (or use Vercel deployment)
2. Ensure both computers have speakers and microphones working
3. Position the computers so the speaker can reach the microphone (typically 1-3 meters apart)

### Step 2: Calibration (Recommended — Python Backend Only)

1. On the **Receiver**, click "Run Calibration"
2. On the **Transmitter**, the calibration signal will play automatically
3. Wait for calibration to complete
4. Review the SNR and recommended settings
5. The system will automatically adjust settings for optimal reliability

### Step 3: Transfer

**On the Transmitter:**
1. Select a file
2. Configure settings (or use calibration recommendations)
3. Click "Start Transmission"
4. The file will be transmitted through audio

**On the Receiver:**
1. The receiver will automatically detect the incoming signal
2. Progress will be shown as frames are received
3. Once complete, the file is downloaded automatically

### Step 4: Verification

The receiver verifies:
- ✓ All frames recovered
- ✓ File hash (SHA-256) verified
- ✓ Authentication verified (if encryption enabled)
- ✓ File reconstructed

## ⚙️ Configuration

### Modulation

- **4-FSK**: 4 frequencies, 2 bits per symbol (default)
- **Symbol Rate**: 150-500 baud (lower = more reliable)

### Frequency Allocation

Default frequencies (optimized for laptop speakers/microphones):
```
Symbol 0 → 1200 Hz
Symbol 1 → 1600 Hz
Symbol 2 → 2000 Hz
Symbol 3 → 2400 Hz
```

### Forward Error Correction (FEC)

Reed-Solomon coding with configurable overhead:
- **10%**: Minimal redundancy (clean environments)
- **25%**: Default (good balance)
- **40%**: High redundancy (noisy environments)

### Encryption (Python Backend Only)

- **None**: No encryption
- **ChaCha20-Poly1305**: Fast, secure (default)
- **AES-256-GCM**: Hardware-accelerated alternative

Key derivation: Argon2id (memory-hard KDF)

## 🧪 Testing

Run the test suite:

```bash
python -m pytest tests/ -v
```

### Test Coverage (42 tests)

- **Protocol**: Packet serialization, CRC, metadata encoding
- **Modulation**: FSK modulation/demodulation, symbol encoding
- **FEC**: Reed-Solomon encoding/decoding, error correction
- **Crypto**: Encryption/decryption, key derivation
- **End-to-End**: Full pipeline with simulated channels

### Simulated Channel Tests

The test suite includes a simulated audio channel that introduces:
- White noise
- Frequency shifts
- Symbol corruption
- Amplitude changes
- Clipping
- Timing drift
- Echoes

## 📁 Project Structure

```
airgapped-audio-filetransfer/
├── backend/                    # Python backend (full features)
│   ├── api/                    # FastAPI REST + WebSocket API
│   │   └── __init__.py
│   ├── crypto/                 # ChaCha20-Poly1305 / AES-256-GCM
│   │   ├── __init__.py
│   │   └── encryption.py
│   ├── devices/                # Audio device management
│   │   ├── __init__.py
│   │   └── audio.py
│   ├── dsp/                    # Digital Signal Processing
│   │   ├── __init__.py
│   │   ├── modulation.py       # FSK modulator
│   │   ├── demodulation.py     # FSK demodulator
│   │   ├── synchronization.py  # Frame sync detection
│   │   ├── spectrum.py         # FFT spectrum analyzer
│   │   ├── calibration.py      # Channel quality measurement
│   │   └── channel.py          # Simulated noisy channel
│   ├── fec/                    # Forward Error Correction
│   │   ├── __init__.py
│   │   └── reed_solomon.py     # Reed-Solomon codec
│   ├── protocol/               # Binary packet protocol
│   │   ├── __init__.py
│   │   └── packet.py           # Frame structure, CRC-16
│   └── transfer/               # Transfer orchestration
│       ├── __init__.py
│       └── manager.py
├── frontend/                   # Web UI (works standalone or with backend)
│   ├── index.html              # Landing page — select Transmitter/Receiver
│   ├── transmitter.html        # Transmitter dashboard
│   ├── receiver.html           # Receiver dashboard
│   ├── css/
│   │   └── main.css            # Dark theme UI
│   └── js/
│       ├── main.js             # Shared utilities
│       ├── modem.js            # Browser FSK modem (Web Audio API)
│       ├── fec.js              # Browser FEC (parity coding)
│       ├── protocol.js         # Browser packet handling
│       └── transfer.js         # Browser transfer manager
├── tests/                      # 42 passing tests
│   ├── test_protocol.py
│   ├── test_modulation.py
│   ├── test_demodulation.py
│   ├── test_fec.py
│   ├── test_crypto.py
│   └── test_end_to_end.py
├── vercel.json                 # Vercel deployment config
├── requirements.txt
├── run.py                      # Python backend entry point
└── README.md
```

## 🔧 Troubleshooting

### No Audio Devices Detected (Python Backend)

```bash
# Check available devices
python -c "import sounddevice as sd; print(sd.query_devices())"
```

### Low SNR / Poor Transfer Quality

1. Reduce distance between speaker and microphone
2. Minimize background noise
3. Use lower symbol rate (150 baud)
4. Increase FEC overhead (40%)
5. Run calibration to find optimal settings

### Transfer Fails

1. Check that both computers are in "air-gapped" mode (no network audio routing)
2. Verify speakers and microphone are working
3. Try calibration first
4. Check the transfer log for errors

### Browser Version Not Working

1. Ensure microphone permission is granted
2. Use Chrome, Edge, or Firefox (Safari may have limitations)
3. Check browser console for errors

## 📊 Performance

### Transfer Speeds

| Symbol Rate | FEC | Effective Speed | Reliability |
|-------------|-----|-----------------|-------------|
| 500 baud    | 10% | ~200 B/s        | Lower       |
| 250 baud    | 25% | ~100 B/s        | Balanced    |
| 150 baud    | 40% | ~50 B/s         | Higher      |

### Example: 1 MB File

- **At 100 B/s**: ~2.8 hours
- **At 200 B/s**: ~1.4 hours

## 🛡️ Security Considerations

1. **Physical Security**: Ensure the transfer environment is secure
2. **Encryption Keys**: Use strong passwords; keys are never transmitted
3. **File Verification**: Always verify SHA-256 hash after transfer
4. **Air-Gap Verification**: Physically verify network cables are disconnected
5. **Logging**: Review transfer logs for any anomalies

## 📝 License

This project is provided as-is for educational and security research purposes.

## 🤝 Contributing

Contributions welcome! Areas for improvement:
- Additional modulation schemes (QPSK, QAM)
- Faster symbol rates
- Bidirectional communication
- Mobile app support
- Hardware acceleration
- Full Reed-Solomon FEC in browser version
