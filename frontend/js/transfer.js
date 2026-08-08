/**
 * Browser-based audio transfer manager.
 *
 * Wire format: one 0.5s sync tone, one alternating 32-symbol preamble,
 * then contiguous protocol frames. Browser FEC is optional parity checking;
 * the Python backend provides Reed-Solomon correction.
 */

class BrowserTransferManager {
    constructor() {
        this.audioContext = null;
        this.modulator = null;
        this.demodulator = null;
        this.fec = null;
        this.state = 'idle';
        this.transferId = null;
        this.selectedFile = null;
        this.cancelled = false;
        this.microphoneStream = null;
        this.mediaSource = null;
        this.analyser = null;
        this.audioProcessor = null;
        this.captureBuffer = [];
        this.captureSamples = 0;
        this.captureOffset = 0;
        this.captureBufferStart = 0;
        this.byteBuffer = new Uint8Array(0);
        this.pendingSymbols = [];
        this.pendingBitValue = 0;
        this.pendingBitCount = 0;
        this.receivedFrames = new Map();
        this.metadata = null;
        this.handshake = null;
        this.seenSync = false;
        this.endReceived = false;
        this.expectedTotalFrames = null;
        this.completionInFlight = false;

        this.lastProcessedSamples = 0;
        this.processedAudioSamples = 0;
        this.processingCapture = false;
        this.completedCapture = false;
        this.onProgress = null;
        this.onComplete = null;
        this.onError = null;
        this.onLog = null;
        this.onCountdown = null;
    }

    async initAudio(requestedSampleRate = 48000) {
        if (!this.audioContext) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            this.audioContext = new AudioContextClass({ sampleRate: requestedSampleRate });
        }
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
        return this.audioContext;
    }

    initModem(sampleRate = 48000, symbolRate = 250,
        frequencies = [1200, 1600, 2000, 2400], fecOverhead = 0) {
        this.modulator = new FSKModulator(sampleRate, symbolRate, frequencies);
        this.demodulator = new FSKDemodulator(sampleRate, symbolRate, frequencies);
        this.fec = new SimpleFEC(fecOverhead);
    }

    async computeHash(data) {
        const digest = await crypto.subtle.digest('SHA-256', data);
        return Array.from(new Uint8Array(digest))
            .map(byte => byte.toString(16).padStart(2, '0')).join('');
    }

    readFile(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(new Uint8Array(reader.result));
            reader.onerror = reject;
            reader.readAsArrayBuffer(file);
        });
    }

    playChunk(audioData) {
        return new Promise((resolve, reject) => {
            try {
                const buffer = this.audioContext.createBuffer(
                    1, audioData.length, this.audioContext.sampleRate);
                buffer.getChannelData(0).set(audioData);
                const source = this.audioContext.createBufferSource();
                source.buffer = buffer;
                source.connect(this.audioContext.destination);
                source.onended = resolve;
                source.start();
            } catch (error) {
                reject(error);
            }
        });
    }

    generateSyncTone(duration = 0.5, frequency = 1000) {
        const sampleRate = this.audioContext.sampleRate;
        const count = Math.floor(sampleRate * duration);
        const audio = new Float32Array(count);
        for (let i = 0; i < count; i++) {
            const envelope = Math.min(1, i / (sampleRate * 0.05)) *
                Math.min(1, (count - i) / (sampleRate * 0.05));
            audio[i] = 0.4 * envelope * Math.sin(
                2 * Math.PI * frequency * i / sampleRate);
        }
        return audio;
    }

    generateBeep(frequency = 800, duration = 0.1) {
        const sampleRate = this.audioContext.sampleRate;
        const count = Math.floor(sampleRate * duration);
        const audio = new Float32Array(count);
        for (let i = 0; i < count; i++) {
            const envelope = Math.min(1, i / (sampleRate * 0.01)) *
                Math.min(1, (count - i) / (sampleRate * 0.01));
            audio[i] = 0.3 * envelope * Math.sin(
                2 * Math.PI * frequency * i / sampleRate);
        }
        return audio;
    }

    async startTransmission(file, options = {}) {
        try {
            await this.initAudio(options.sampleRate || 48000);
            this.cancelled = false;
            if (this.audioContext.sampleRate !== (options.sampleRate || 48000)) {
                throw new Error(`Audio sample rate ${this.audioContext.sampleRate} Hz does not match requested wire rate`);
            }
            const symbolRate = options.symbolRate || 250;
            const frequencies = options.frequencies || [1200, 1600, 2000, 2400];
            const fecOverhead = Number(options.fecOverhead || 0);
            const fecEnabled = fecOverhead > 0;
            this.initModem(this.audioContext.sampleRate, symbolRate, frequencies,
                fecEnabled ? fecOverhead : 0);
            this.selectedFile = file;
            this.state = 'transmitting';
            this.transferId = Math.floor(Math.random() * 0xFFFFFFFF);

            this.log('Reading file...');
            const fileData = await this.readFile(file);
            this.log('Computing hash...');
            const fileHash = await this.computeHash(fileData);
            const chunkSize = 128;
            const totalChunks = Math.max(1, Math.ceil(fileData.length / chunkSize));
            const fecAlgorithm = fecEnabled ? 'xor-parity' : 'none';
            const metadata = {
                filename: file.name,
                filesize: file.size,
                mimeType: file.type || 'application/octet-stream',
                chunkSize,
                totalChunks,
                hashAlgorithm: 'sha256',
                fileHash,
                compressionEnabled: false,
                encryptionEnabled: false,
                fecOverhead: fecEnabled ? fecOverhead / 100 : 0,
                fecEnabled,
                fecAlgorithm
            };

            this.log('Encoding frames...');
            const frames = [];
            const totalFrames = totalChunks + 4;
            frames.push(new Frame(FrameType.SYNC, this.transferId, 0, totalFrames));
            frames.push(new Frame(FrameType.HANDSHAKE, this.transferId, 0, totalFrames,
                encodeHandshake({
                    sampleRate: this.audioContext.sampleRate,
                    symbolRate,
                    bitsPerSymbol: Math.log2(frequencies.length),
                    frequencies,
                    fecOverhead: metadata.fecOverhead,
                    fecEnabled,
                    fecAlgorithm,
                    syncPreambleSymbols: 32,
                    syncFrequency: 1000
                })));
            frames.push(new Frame(FrameType.METADATA, this.transferId, 0, totalFrames,
                encodeMetadata(metadata)));

            for (let i = 0; i < totalChunks; i++) {
                const chunk = fileData.slice(i * chunkSize, (i + 1) * chunkSize);
                frames.push(new Frame(FrameType.DATA, this.transferId, i,
                    totalFrames, this.fec.encode(chunk)));
            }
            frames.push(new Frame(FrameType.END, this.transferId, 0, totalFrames));

            this.log('Starting in 3 seconds...');
            for (let i = 3; i >= 1; i--) {
                if (this.cancelled) return;
                if (this.onCountdown) this.onCountdown(i);
                await this.playChunk(this.generateBeep(i === 1 ? 1000 : 600, 0.15));
                await new Promise(resolve => setTimeout(resolve, 800));
            }

            this.log('Playing sync tone...');
            await this.playChunk(this.generateSyncTone(0.5, 1000));
            await this.playChunk(this.modulator.modulateSymbols(
                Array.from({ length: 32 }, (_, index) => index % 2)));

            this.log(`Transmitting ${frames.length} frames...`);
            let dataBytesSent = 0;
            for (let i = 0; i < frames.length; i++) {
                if (this.cancelled) return;
                const frame = frames[i];
                await this.playChunk(this.modulator.modulateBytes(frame.serialize()));
                if (frame.type === FrameType.DATA) dataBytesSent += frame.payload.length;
                if (this.onProgress) {
                    this.onProgress({
                        progress: (i + 1) / frames.length,
                        bytesSent: Math.min(dataBytesSent, file.size),
                        totalBytes: file.size,
                        framesSent: i + 1,
                        totalFrames: frames.length
                    });
                }
            }

            this.state = 'complete';
            this.log('Transmission complete!');
            if (this.onComplete) this.onComplete({
                success: true, filename: file.name, hash: fileHash
            });
        } catch (error) {
            this.state = 'idle';
            this.log(`Error: ${error.message}`, 'ERROR');
            if (this.onError) this.onError(error);
        }
    }

    async startReceiving(options = {}) {
        try {
            await this.initAudio(options.sampleRate || 48000);
            this.cancelled = false;
            if (this.audioContext.sampleRate !== (options.sampleRate || 48000)) {
                throw new Error(`Audio sample rate ${this.audioContext.sampleRate} Hz does not match requested wire rate`);
            }
            this.completedCapture = false;
            this.processingCapture = false;
            const symbolRate = options.symbolRate || 250;
            const frequencies = options.frequencies || [1200, 1600, 2000, 2400];
            const fecOverhead = Number(options.fecOverhead || 0);
            this.initModem(this.audioContext.sampleRate, symbolRate, frequencies,
                fecOverhead);
            this.state = 'receiving';
            this.log('Starting microphone...');
            this.microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaSource = this.audioContext.createMediaStreamSource(this.microphoneStream);
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 2048;
            this.audioProcessor = this.audioContext.createScriptProcessor(4096, 1, 1);
            const silentOutput = this.audioContext.createGain();
            silentOutput.gain.value = 0;
            this.mediaSource.connect(this.analyser);
            this.mediaSource.connect(this.audioProcessor);
            this.audioProcessor.connect(silentOutput);
            silentOutput.connect(this.audioContext.destination);
            this.captureBuffer = [];
            this.captureSamples = 0;
            this.captureOffset = 0;
            this.captureBufferStart = 0;
            this.byteBuffer = new Uint8Array(0);
            this.pendingSymbols = [];
            this.pendingBitValue = 0;
            this.pendingBitCount = 0;
            this.receivedFrames = new Map();
            this.metadata = null;
            this.handshake = null;
            this.seenSync = false;
            this.endReceived = false;
            this.expectedTotalFrames = null;
            this.completionInFlight = false;
            this.lastProcessedSamples = 0;
            this.processedAudioSamples = 0;
            this.audioProcessor.onaudioprocess = event => this.captureAudio(event);
            this.log('Listening for transmission...');
        } catch (error) {
            this.state = 'idle';
            this.finishCapture();
            this.log(`Error: ${error.message}`, 'ERROR');
            if (this.onError) this.onError(error);
        }
    }

    captureAudio(event) {
        if (this.state !== 'receiving' || this.cancelled) return;
        const input = event.inputBuffer.getChannelData(0);
        const chunk = new Float32Array(input);
        this.captureBuffer.push(chunk);
        this.captureSamples += chunk.length;
        if (this.onProgress) {
            const rms = Math.sqrt(chunk.reduce((sum, value) => sum + value * value, 0) /
                chunk.length);
            this.onProgress({
                levelDb: 20 * Math.log10(rms + 1e-10),
                samplesReceived: this.captureSamples
            });
        }
        if (!this.processingCapture && !this.completedCapture &&
            this.captureSamples - this.lastProcessedSamples >= this.audioContext.sampleRate * 2) {
            this.processingCapture = true;
            this.lastProcessedSamples = this.captureSamples;
            this.processCapturedAudio();
        }
    }

    processCapturedAudio() {
        const sampleParts = this.captureBuffer;
        const available = this.captureSamples - this.captureBufferStart - this.captureOffset;
        if (available <= 0) {
            this.processingCapture = false;
            return;
        }
        const captured = new Float32Array(available);
        let copied = 0;
        let sourceOffset = this.captureBufferStart;
        for (const part of sampleParts) {
            const partEnd = sourceOffset + part.length;
            const targetOffset = this.captureBufferStart + this.captureOffset;
            if (partEnd <= targetOffset) {
                sourceOffset = partEnd;
                continue;
            }
            const start = Math.max(0, targetOffset - sourceOffset);
            const count = Math.min(part.length - start, available - copied);
            captured.set(part.subarray(start, start + count), copied);
            copied += count;
            sourceOffset = partEnd;
            if (copied === available) break;
        }
        const consumed = this.processReceivedAudio(captured);
        this.captureOffset += consumed;
        let removed = 0;
        while (this.captureBuffer.length && removed + this.captureBuffer[0].length <= this.captureOffset) {
            removed += this.captureBuffer[0].length;
            this.captureBuffer.shift();
        }
        if (removed) {
            this.captureOffset -= removed;
            this.captureBufferStart += removed;
        }
    }

    processReceivedAudio(audioData) {
        if (!audioData || audioData.length === 0) {
            this.processingCapture = false;
            return 0;
        }
        const syncSamples = Math.floor(this.audioContext.sampleRate * 0.5);
        const preambleSamples = 32 * this.demodulator.samplesPerSymbol;
        const dataOffset = this.seenSync ? 0 : syncSamples + preambleSamples;
        if (audioData.length <= dataOffset) {
            this.processingCapture = false;
            return 0;
        }
        const symbols = this.demodulator.demodulateSymbols(audioData, dataOffset).symbols;
        if (!symbols.length) {
            this.processingCapture = false;
            return 0;
        }
        const consumed = dataOffset + symbols.length * this.demodulator.samplesPerSymbol;
        if (!this.seenSync) this.seenSync = true;
        const bytes = [];
        const mask = (1 << this.demodulator.bitsPerSymbol) - 1;
        for (const symbol of symbols) {
            this.pendingBitValue = (this.pendingBitValue << this.demodulator.bitsPerSymbol) |
                (symbol & mask);
            this.pendingBitCount += this.demodulator.bitsPerSymbol;
            while (this.pendingBitCount >= 8) {
                this.pendingBitCount -= 8;
                bytes.push((this.pendingBitValue >> this.pendingBitCount) & 0xFF);
                this.pendingBitValue &= this.pendingBitCount ?
                    (1 << this.pendingBitCount) - 1 : 0;
            }
        }
        const decodedBytes = new Uint8Array(bytes);
        const merged = new Uint8Array(this.byteBuffer.length + decodedBytes.length);
        merged.set(this.byteBuffer);
        merged.set(decodedBytes, this.byteBuffer.length);
        this.byteBuffer = merged;

        let offset = 0;
        while (this.byteBuffer.length - offset >= 20) {
            const payloadLength = (this.byteBuffer[offset + 18] << 8) |
                this.byteBuffer[offset + 19];
            const frameLength = 22 + payloadLength;
            if (payloadLength > 2048) {
                offset += 1;
                continue;
            }
            if (this.byteBuffer.length - offset < frameLength) break;
            const frameBytes = this.byteBuffer.slice(offset, offset + frameLength);
            const frame = Frame.deserialize(frameBytes);
            if (!frame) {
                offset += 1;
                continue;
            }
            this.handleReceivedFrame(frame);
            offset += frameLength;
        }
        if (offset > 0) this.byteBuffer = this.byteBuffer.slice(offset);
        this.tryCompleteReceive();
        this.processingCapture = false;
        return consumed;
    }

    handleReceivedFrame(frame) {
        if (frame.type === FrameType.SYNC) {
            this.transferId = frame.transferId;
            this.expectedTotalFrames = frame.totalFrames;
            if (!this.expectedTotalFrames || this.expectedTotalFrames < 4) {
                throw new Error('Invalid total frame count');
            }
        } else if (this.transferId !== frame.transferId) {
            throw new Error('Frame belongs to a different transfer');
        } else if (frame.totalFrames !== this.expectedTotalFrames) {
            throw new Error('Frame total count changed mid-stream');
        } else if (frame.type === FrameType.HANDSHAKE) {
            this.handshake = decodeHandshake(frame.payload);
            if (this.handshake.sampleRate !== this.modulator.sampleRate ||
                this.handshake.symbolRate !== this.modulator.symbolRate ||
                this.handshake.bitsPerSymbol !== this.modulator.bitsPerSymbol ||
                this.handshake.syncPreambleSymbols !== 32 ||
                this.handshake.syncFrequency !== 1000 ||
                this.handshake.frequencies.some((frequency, index) =>
                    frequency !== this.modulator.frequencies[index])) {
                throw new Error('Handshake does not match receiver configuration');
            }
        } else if (frame.type === FrameType.METADATA) {
            this.metadata = decodeMetadata(frame.payload);
            if (this.metadata.fecAlgorithm !== 'none' && this.metadata.fecAlgorithm !== 'xor-parity') {
                throw new Error(`Unsupported browser FEC algorithm: ${this.metadata.fecAlgorithm}`);
            }
            this.fec = new SimpleFEC(this.metadata.fecAlgorithm === 'xor-parity' &&
                this.metadata.fecEnabled !== false ? (this.metadata.fecOverhead || 0) * 100 : 0);
            if (this.onProgress) this.onProgress({
                type: 'metadata', filename: this.metadata.filename,
                filesize: this.metadata.filesize
            });
        } else if (frame.type === FrameType.DATA) {
            if (!this.metadata || frame.sequenceNumber < 0 ||
                frame.sequenceNumber >= this.metadata.totalChunks) {
                throw new Error('Data frame sequence is out of range');
            }
            if (this.receivedFrames.has(frame.sequenceNumber)) {
                throw new Error('Duplicate data frame');
            }
            this.receivedFrames.set(frame.sequenceNumber, frame);
        } else if (frame.type === FrameType.END) {
            this.endReceived = true;
        }
    }

    tryCompleteReceive() {
        if (!this.metadata || !this.endReceived ||
            this.receivedFrames.size !== this.metadata.totalChunks ||
            this.completionInFlight) return;
        this.completionInFlight = true;
        const chunks = [];
        for (let i = 0; i < this.metadata.totalChunks; i++) {
            const frame = this.receivedFrames.get(i);
            if (!frame) return;
            const decoded = this.fec.decode(frame.payload);
            if (!decoded.valid) {
                this.log(`FEC validation failed for chunk ${i}`, 'ERROR');
                this.finishCapture();
                return;
            }
            chunks.push(decoded.data);
        }
        const fileData = new Uint8Array(chunks.reduce((sum, chunk) => sum + chunk.length, 0));
        let dataOffset = 0;
        for (const chunk of chunks) {
            fileData.set(chunk, dataOffset);
            dataOffset += chunk.length;
        }
        if (fileData.length !== this.metadata.filesize) {
            this.log(`Size verification failed: expected ${this.metadata.filesize}, got ${fileData.length}`, 'ERROR');
            return;
        }
        this.computeHash(fileData).then(hash => {
            const verified = hash === this.metadata.fileHash;
            this.state = verified ? 'complete' : 'idle';
            this.completedCapture = verified;
            this.log(`Hash verified: ${verified}`);
            if (this.onComplete) this.onComplete({
                success: verified, filename: this.metadata.filename,
                hash, filesize: fileData.length
            });
            if (verified) this.downloadFile(fileData, this.metadata.filename);
            this.finishCapture();
        }).catch(error => {
            this.log(`Hash error: ${error.message}`, 'ERROR');
            if (this.onError) this.onError(error);
        });
    }

    downloadFile(data, filename) {
        const url = URL.createObjectURL(new Blob([data]));
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    finishCapture() {
        if (this.microphoneStream) {
            this.microphoneStream.getTracks().forEach(track => track.stop());
            this.microphoneStream = null;
        }
        if (this.mediaSource) {
            this.mediaSource.disconnect();
            this.mediaSource = null;
        }
        if (this.audioProcessor) {
            this.audioProcessor.onaudioprocess = null;
            this.audioProcessor.disconnect();
            this.audioProcessor = null;
        }
        if (this.analyser) {
            this.analyser.disconnect();
            this.analyser = null;
        }
        this.captureBuffer = [];
        this.captureSamples = 0;
        this.byteBuffer = new Uint8Array(0);
        this.pendingSymbols = [];
        this.captureOffset = 0;
        this.captureBufferStart = 0;
        this.completionInFlight = false;
    }

    cancel() {
        this.cancelled = true;
        this.state = 'idle';
        this.finishCapture();
        this.log('Transfer cancelled');
    }

    log(message, level = 'INFO') {
        console.log(`[${level}] ${message}`);
        if (this.onLog) this.onLog({ message, level });
    }
}

window.BrowserTransferManager = BrowserTransferManager;
