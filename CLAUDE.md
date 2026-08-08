# CLAUDE.md

## Project overview

This repository implements an air-gapped file-transfer system that encodes files as audio and decodes them through a microphone.

- `backend/`: Python implementation with FastAPI, DSP, protocol framing, Reed-Solomon FEC, encryption, and simulated channels.
- `frontend/`: standalone browser implementation using Web Audio API.
- `tests/`: Python unit, integration, protocol, crypto, FEC, DSP, and transfer-manager tests.
- `run.py`: local FastAPI launcher.
- `vercel.json`: static frontend deployment configuration.

The project has two related implementations. Keep their wire formats synchronized when changing protocol fields or frame behavior.

## Important files

- `backend/protocol/packet.py`: frame layout, CRC-16, metadata, and `ProtocolConfig` validation.
- `backend/transfer/manager.py`: file preparation, modulation, streaming receiver parser, FEC/decryption/decompression, and hash verification.
- `backend/dsp/modulation.py`: Python FSK byte/symbol packing and waveform generation.
- `backend/dsp/demodulation.py`: Python Goertzel-based symbol detection.
- `backend/api/__init__.py`: FastAPI routes, WebSocket control, simulated transfer lifecycle.
- `frontend/js/protocol.js`: browser frame and metadata serialization.
- `frontend/js/modem.js`: browser FSK modem.
- `frontend/js/transfer.js`: browser transmission, microphone capture, parsing, verification, and download.

## Development workflow

1. Read the surrounding implementation and tests before editing.
2. Preserve existing public names and wire compatibility unless the task explicitly changes the protocol.
3. Search all references before changing an exported function, class, field, or method signature.
4. Make focused edits; do not modify generated files, `node_modules`, lockfiles, or unrelated user changes.
5. Add a regression test for every confirmed bug.
6. Run the validation commands below before reporting completion.

Do not commit, push, deploy, install global packages, or alter production environments unless explicitly requested.

## Python setup and commands

Prerequisites are Python 3.10+ and the dependencies in `requirements.txt`.

Run the complete Python test suite:

```bash
python -m pytest -q
```

Run focused tests while iterating:

```bash
python -m pytest -q tests/test_protocol.py tests/test_demodulation.py tests/test_transfer_manager.py
```

Compile-check Python files:

```bash
python -m compileall -q backend run.py
```

Run the local backend:

```bash
python run.py --mode transmitter
python run.py --mode receiver
```

The backend should bind to `127.0.0.1` by default. It exposes local REST and WebSocket controls and serves the frontend pages.

## JavaScript validation

There is no meaningful npm test script in `package.json`. Check every frontend file with Node:

```bash
for f in frontend/js/*.js; do node --check "$f" || exit 1; done
```

Browser runtime checks can use a Node VM smoke test for protocol serialization. Full browser automation may not be available if Chrome/Chromium is absent.

## Protocol rules

The binary frame layout is:

```text
MAGIC(4) VERSION(1) TRANSFER_ID(4) FRAME_TYPE(1)
SEQ_NUM(4) TOTAL_FRAMES(4) PAYLOAD_LEN(2) PAYLOAD(var) CRC(2)
```

- `MAGIC` is `ATFR`.
- CRC-16/CCITT covers the header and payload, excluding the CRC field.
- Maximum frame payload is 2048 bytes.
- Metadata must describe filename, file size, chunk size, total chunks, MIME type, hash algorithm, file hash, compression, and encryption settings.
- FEC metadata includes both overhead and whether FEC is enabled.
- The receiver verifies the reconstructed file with SHA-256.
- The Python and browser implementations must use the same field order and integer endianness.

### FSK constraints

- Frequencies must be a power-of-two count and match `bits_per_symbol`.
- Default modulation is 4-FSK: `[1200, 1600, 2000, 2400]`, 2 bits/symbol.
- Non-byte-aligned modulation such as 8-FSK uses bit accumulation and zero-padding only for the final symbol.
- Frequencies must map to unique Goertzel detector bins at the configured symbol rate. A visually distinct frequency list can still be invalid if two frequencies round to the same detector bin.
- Frame audio length must be calculated from `ceil(byte_count * 8 / bits_per_symbol)`, not a hardcoded four symbols per byte.
- The transfer stream uses one sync tone and one alternating preamble per transfer, followed by contiguous frame audio.

### FEC compatibility

Python uses Reed-Solomon. Browser `SimpleFEC` is parity-based validation only and is not interchangeable with Python Reed-Solomon correction. Do not advertise browser parity as Reed-Solomon correction or enable cross-platform FEC without an explicit compatible algorithm/wire format.

## State and resource safety

- `TransferManager.configure()` and `load_file()` must clear stale frame/file/cancellation state when starting a new session.
- Completed, cancelled, and errored receivers should ignore subsequent audio chunks.
- Receiver audio buffers need a bounded-memory policy.
- API transfer tasks must be tracked so duplicate starts and cancellation are handled deterministically.
- Blocking audio operations should not block the asyncio event loop. Cancellation may still require an explicit audio-device stop path.
- Avoid using mutable global `app_state.manager`, config, or file paths as implicit task state; capture session-local references where possible.
- Browser microphone tracks, audio nodes, and processors must be disconnected/stopped on completion, cancellation, and errors.

## Performance guidance

The current architecture still builds complete file/frame/audio buffers for transmission, and browser capture can retain a growing recording. Treat large-file streaming as a separate design task rather than casually increasing buffer limits.

Likely DSP hotspots are Python Goertzel loops, repeated Hamming-window allocation, repeated receiver buffer copies, and browser re-demodulation of the full capture. Optimize only with benchmarks and preserve framing alignment.

## Security guidance

- Keep encryption keys out of metadata and the audio protocol.
- Preserve authenticated encryption and chunk-index associated data.
- Validate upload names with `Path(filename).name`; never trust a client path.
- Keep CORS restricted to localhost unless the threat model is deliberately changed.
- Validate booleans as actual booleans; do not use `bool("false")`-style coercion.
- Do not weaken CRC, file-size, frame-count, transfer-ID, or SHA-256 verification.

## Before finishing a change

Run:

```bash
python -m pytest -q
for f in frontend/js/*.js; do node --check "$f" || exit 1; done
python -m compileall -q backend run.py
git diff --check
```

Also run a focused transmitter-to-receiver round trip when changing protocol, DSP, FEC, compression, encryption, or transfer-manager code. Report any environment limitation, especially missing audio hardware or missing Chrome/Chromium.
