# Airgap Audio Transfer

A local web application for moving arbitrary files between two physically
isolated computers using the only intended data path:

```text
speaker -> air -> microphone
```

The browser is a **control plane only**. It talks to a backend on the same
computer at `127.0.0.1`. File bytes are never sent to the other computer over
HTTP, WebSocket, Ethernet, Wi-Fi, Bluetooth, or a cloud service.

## Status

The core modem and file protocol are implemented and tested end-to-end through
a simulated noisy channel. The local dashboard supports transmitter and
receiver operation, device discovery, calibration status, progress telemetry,
local event-log export, and real PortAudio input/output through `sounddevice`.

This is an engineering tool, not a substitute for an independently audited
high-assurance data diode. Test your own speakers, microphones, room, and
operating-system audio processing before trusting a transfer.

## Install

Use an environment installed separately on each computer. Package installation
should be performed before disconnecting the machines from networks.

```bash
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows Git Bash
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

PortAudio is required by `sounddevice`. On Linux, install the distribution's
PortAudio development/runtime package before installing Python dependencies.
On Windows and macOS, the `sounddevice` wheel normally supplies what is needed,
but the OS may still ask for microphone permission.

## Run two isolated computers

### Computer A — transmitter

1. Disable Wi-Fi, Ethernet, Bluetooth, VPNs, and other network interfaces.
2. Connect speakers/headphones as the output device.
3. Start the local control plane:

   ```bash
   python run.py --mode transmitter
   ```

4. Select a file, choose a profile, run calibration, and enter an out-of-band
   password if encryption is enabled.
5. Put the speaker near the receiver microphone and start transmission.

### Computer B — receiver

1. Disable Wi-Fi, Ethernet, Bluetooth, VPNs, and other network interfaces.
2. Connect or select the microphone that hears Computer A's speaker.
3. Start the receiver locally:

   ```bash
   python run.py --mode receiver
   ```

4. Enter the **same password manually** if the transmitter used password mode.
   The password is never transmitted through audio.
5. Start listening before starting the transmitter. The receiver writes to a
   local `received/` directory by default and only reports success after the
   SHA-256 file hash is verified.

Use `--port` to run multiple local instances on one development computer; do
not expose the port beyond localhost.

## Protocol

The data plane is deliberately modular:

```text
file -> bounded chunks -> optional zstd/gzip -> ChaCha20-Poly1305
     -> Reed-Solomon parity -> CRC frames -> 4-FSK samples -> speaker
```

The receiver reverses those stages. The frame header contains:

```text
magic | protocol version | frame type | transfer id | sequence |
 total frames | payload length | payload | CRC32
```

Frame types include `SYNC`, `HANDSHAKE`, `METADATA`, `DATA`, `PARITY`, `END`,
`ACK`, and `ERROR`. ACK is reserved for a future reverse acoustic channel;
the current protocol is one-way and does not need TCP/IP retransmission.

### Modem defaults

* 48,000 Hz mono audio
* 300 baud balanced profile
* four-frequency FSK at 1,200 / 1,800 / 2,400 / 3,000 Hz
* 16 KiB source chunks
* 25% Reed-Solomon parity overhead
* CRC32 per frame plus final SHA-256

Profiles trade rate for robustness:

| Profile | Baud | FEC | Use |
| --- | ---: | ---: | --- |
| Maximum reliability | 180 | 40% | noisy rooms or weak speakers |
| Balanced | 300 | 25% | default |
| Maximum speed | 500 | 10% | short, quiet links only |

The receiver treats CRC failures as erasures. Reed-Solomon operates on groups
of fixed-size encrypted chunk shards, so it can repair missing/corrupted data
without any network ACK. Only one FEC group is buffered while a file is
received.

## Encryption

ChaCha20-Poly1305 provides authenticated encryption for each independent
chunk. Password mode derives a 256-bit key using Argon2id with a random salt
(`argon2-cffi`; a clearly marked scrypt fallback exists for minimal installs).
Pre-shared-key mode accepts exactly 32 bytes supplied out-of-band.

The audio metadata can contain a salt and nonce prefix, but never contains the
password or key. Nonces are derived from the nonce prefix and chunk sequence;
associated data binds each ciphertext to its transfer ID and sequence number.
A wrong key fails authenticated decryption and the final hash can never pass.

## Calibration and visualization

The dashboard exposes a link-test panel and displays signal confidence, SNR,
noise floor, clipping, transfer progress, FEC recovery, local event logs, and a
lightweight waveform monitor. Keep speakers and microphones aligned, avoid
automatic OS microphone gain if possible, and prefer Maximum Reliability when
in doubt.

## Development and tests

The first development phase is independently testable without microphones:

```text
bytes -> packet frames -> FSK samples -> simulated noisy channel -> FSK
samples -> frames -> bytes
```

Run the suite with:

```bash
python -m pytest -q
```

The tests cover frame CRC/versioning, GF(256) Reed-Solomon recovery, actual FSK
sample demodulation with noise and level changes, authenticated encryption,
and a streaming file round trip with deliberately erased data frames.

The simulated modem can be extended with timing drift, frequency offset,
clipping, echoes, and reverberation by transforming the NumPy sample array
between `modulate_frames` and `demodulate_frames` in
`tests/test_modulation.py`.

## Project layout

```text
backend/
  main.py             localhost FastAPI control plane
  audio_io.py         local PortAudio device integration
  protocol/frames.py  versioned frame format and CRC
  dsp/                FSK modem and calibration
  fec/                Reed-Solomon erasure coding
  crypto/             Argon2id and ChaCha20-Poly1305
  transfer/           bounded-memory file sessions
frontend/              local dashboard
run.py                 localhost launcher
tests/                 simulated and unit tests
```
