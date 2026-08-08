# Air-Gapped Audio File Transfer System

Transfer files between physically isolated computers using **only sound**. No network, no cables, no Bluetooth — just speakers and microphones.

## 🔒 Security

- **100% Air-Gapped**: The only data channel is speaker → air → microphone
- **No Network Exposure**: Backend binds to `127.0.0.1` only
- **End-to-End Encryption**: Optional ChaCha20-Poly1305 or AES-256-GCM
- **Zero Telemetry**: No uploads, no external APIs, no cloud services
- **Keys Never Transmitted**: Passwords/keys stay on each machine

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
│Encrypt  │                          │FEC Decode   │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐                          ┌─────────────┐
│FEC Encode│                         │Decrypt      │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐                          ┌─────────────┐
│Modulate │                          │Reassemble   │
└────┬────┘                          └──────┬──────┘
     │                                       │
     ▼                                       ▼
┌─────────┐       ))) AIR (((       ┌─────────────┐
│Speaker  │ ─────────────────────── │  File       │
└─────────┘                          └─────────────┘
```

## 📦 Installation

### Prerequisites

- Python 3.10+
- pip
- Audio devices (speakers and microphone)

### Setup

```bash
# Clone or download the project
cd audio-transfer

# Install dependencies
pip install -r requirements.txt
```

### Requirements

```
fastapi==0.109.0
uvicorn[standard]==0.27.0
websockets==12.0
numpy==1.26.3
scipy==1.12.0
cryptography==42.0.2
zstandard==0.22.0
python-multipart==0.0.6
sounddevice==0.4.6
reedsolo==1.5.11
```

## 🚀 Usage

### Starting the Transmitter

```bash
python run.py --mode transmitter
```

This opens a browser to `http://127.0.0.1:8000` with the transmitter dashboard.

### Starting the Receiver

```bash
python run.py --mode receiver
```

This opens a browser to `http://127.0.0.1:8000` with the receiver dashboard.

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

1. Install the application on both computers
2. Ensure both computers have speakers and microphones working
3. Position the computers so the speaker can reach the microphone (typically 1-3 meters apart)

### Step 2: Calibration (Recommended)

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
3. Once complete, verify the file hash matches

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

### Encryption

- **None**: No encryption
- **ChaCha20-Poly1305**: Fast, secure (default)
- **AES-256-GCM**: Hardware-accelerated alternative

Key derivation: Argon2id (memory-hard KDF)

## 🧪 Testing

Run the test suite:

```bash
python -m pytest tests/ -v
```

### Test Coverage

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
audio-transfer/
├── backend/
│   ├── __init__.py
│   ├── api/              # FastAPI backend
│   │   └── __init__.py
│   ├── crypto/           # Encryption
│   │   ├── __init__.py
│   │   └── encryption.py
│   ├── devices/          # Audio device management
│   │   ├── __init__.py
│   │   └── audio.py
│   ├── dsp/              # Digital Signal Processing
│   │   ├── __init__.py
│   │   ├── modulation.py
│   │   ├── demodulation.py
│   │   ├── synchronization.py
│   │   ├── spectrum.py
│   │   ├── calibration.py
│   │   └── channel.py
│   ├── fec/              # Forward Error Correction
│   │   ├── __init__.py
│   │   └── reed_solomon.py
│   ├── protocol/         # Packet structure
│   │   ├── __init__.py
│   │   └── packet.py
│   └── transfer/         # Transfer management
│       ├── __init__.py
│       └── manager.py
├── frontend/
│   ├── index.html        # Landing page
│   ├── transmitter.html  # Transmitter dashboard
│   ├── receiver.html     # Receiver dashboard
│   ├── css/
│   │   └── main.css
│   └── js/
│       └── main.js
├── tests/
│   ├── test_protocol.py
│   ├── test_modulation.py
│   ├── test_demodulation.py
│   ├── test_fec.py
│   ├── test_crypto.py
│   └── test_end_to_end.py
├── requirements.txt
├── run.py
└── README.md
```

## 🔧 Troubleshooting

### No Audio Devices Detected

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
