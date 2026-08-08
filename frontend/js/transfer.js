/**
 * Browser-based Transfer Manager using Web Audio API.
 * 
 * Handles the complete file transfer pipeline in the browser.
 */

class BrowserTransferManager {
    constructor() {
        this.audioContext = null;
        this.modulator = null;
        this.demodulator = null;
        this.fec = null;
        
        this.state = 'idle'; // idle, transmitting, receiving, complete
        this.transferId = null;
        this.selectedFile = null;
        
        // Callbacks
        this.onProgress = null;
        this.onComplete = null;
        this.onError = null;
        this.onLog = null;
    }

    /**
     * Initialize audio context.
     */
    async initAudio() {
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        
        // Resume if suspended (required after user interaction)
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
        
        return this.audioContext;
    }

    /**
     * Initialize modem and FEC.
     */
    initModem(sampleRate = 48000, symbolRate = 250, frequencies = [1200, 1600, 2000, 2400]) {
        this.modulator = new FSKModulator(sampleRate, symbolRate, frequencies);
        this.demodulator = new FSKDemodulator(sampleRate, symbolRate, frequencies);
        this.fec = new SimpleFEC(25);
    }

    /**
     * Compute SHA-256 hash of data.
     */
    async computeHash(data) {
        const hashBuffer = await crypto.subtle.digest('SHA-256', data);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    }

    /**
     * Read file as ArrayBuffer.
     */
    readFile(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(new Uint8Array(reader.result));
            reader.onerror = reject;
            reader.readAsArrayBuffer(file);
        });
    }

    /**
     * Start transmission.
     */
    async startTransmission(file, options = {}) {
        try {
            await this.initAudio();
            
            const symbolRate = options.symbolRate || 250;
            const frequencies = options.frequencies || [1200, 1600, 2000, 2400];
            this.initModem(this.audioContext.sampleRate, symbolRate, frequencies);

            this.selectedFile = file;
            this.state = 'transmitting';
            this.transferId = Math.floor(Math.random() * 0xFFFFFFFF);

            this.log('Reading file...');
            const fileData = await this.readFile(file);
            
            this.log('Computing hash...');
            const fileHash = await this.computeHash(fileData);

            // Create metadata
            const metadata = {
                filename: file.name,
                filesize: file.size,
                mimeType: file.type || 'application/octet-stream',
                chunkSize: 128,
                totalChunks: Math.ceil(fileData.length / 128),
                hashAlgorithm: 'sha-256',
                fileHash: fileHash,
                compressionEnabled: false,
                encryptionEnabled: false
            };

            this.log('Encoding metadata...');
            const metadataPayload = encodeMetadata(metadata);
            
            // Create frames
            const frames = [];

            // Handshake frame
            const handshakePayload = new Uint8Array([192, 0, 0, symbolRate, 2, frequencies.length, 25]);
            frames.push(new Frame(FrameType.HANDSHAKE, this.transferId, 0, 0, handshakePayload));

            // Metadata frame
            frames.push(new Frame(FrameType.METADATA, this.transferId, 0, 0, metadataPayload));

            // Data frames
            const chunkSize = 128;
            const totalChunks = Math.ceil(fileData.length / chunkSize);
            
            for (let i = 0; i < totalChunks; i++) {
                const start = i * chunkSize;
                const end = Math.min(start + chunkSize, fileData.length);
                const chunk = fileData.slice(start, end);

                // Apply FEC
                const encodedChunk = this.fec.encode(chunk);

                // Create data frame
                frames.push(new Frame(FrameType.DATA, this.transferId, i, totalChunks, encodedChunk));

                // Update progress
                if (this.onProgress) {
                    this.onProgress({
                        progress: (i + 1) / totalChunks,
                        bytesSent: end,
                        totalBytes: file.size,
                        framesSent: i + 2,
                        totalFrames: totalChunks + 2
                    });
                }
            }

            // End frame
            frames.push(new Frame(FrameType.END, this.transferId, 0, totalChunks));

            // Modulate all frames to audio
            this.log('Modulating audio...');
            let allAudio = new Float32Array(0);

            for (const frame of frames) {
                const frameData = frame.serialize();
                const audio = this.modulator.modulateBytes(frameData);
                
                // Add sync tone
                const audioWithSync = this.modulator.addSyncTone(audio, 0.1, 1000);
                
                // Concatenate
                const newAllAudio = new Float32Array(allAudio.length + audioWithSync.length);
                newAllAudio.set(allAudio, 0);
                newAllAudio.set(audioWithSync, allAudio.length);
                allAudio = newAllAudio;
            }

            // Play audio
            this.log('Playing audio...');
            await this.playAudio(allAudio);

            this.state = 'complete';
            this.log('Transmission complete!');

            if (this.onComplete) {
                this.onComplete({
                    success: true,
                    filename: file.name,
                    hash: fileHash
                });
            }

        } catch (error) {
            this.state = 'idle';
            this.log('Error: ' + error.message, 'ERROR');
            if (this.onError) {
                this.onError(error);
            }
        }
    }

    /**
     * Play audio buffer through speakers.
     */
    playAudio(audioData) {
        return new Promise((resolve) => {
            const buffer = this.audioContext.createBuffer(1, audioData.length, this.audioContext.sampleRate);
            buffer.getChannelData(0).set(audioData);

            const source = this.audioContext.createBufferSource();
            source.buffer = buffer;
            source.connect(this.audioContext.destination);
            source.onended = resolve;
            source.start();
        });
    }

    /**
     * Start receiving.
     */
    async startReceiving(options = {}) {
        try {
            await this.initAudio();
            
            const symbolRate = options.symbolRate || 250;
            const frequencies = options.frequencies || [1200, 1600, 2000, 2400];
            this.initModem(this.audioContext.sampleRate, symbolRate, frequencies);

            this.state = 'receiving';
            this.log('Starting microphone...');

            // Get microphone access
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const source = this.audioContext.createMediaStreamSource(stream);
            const analyser = this.audioContext.createAnalyser();
            analyser.fftSize = 2048;
            source.connect(analyser);

            // Start recording audio
            this.log('Listening for transmission...');
            
            // For simplicity, we'll record for a fixed duration
            // In a real implementation, you'd detect the sync tone and record dynamically
            const duration = 30; // seconds
            const sampleRate = this.audioContext.sampleRate;
            const totalSamples = duration * sampleRate;
            const recordedData = new Float32Array(totalSamples);
            
            let sampleIndex = 0;
            const dataArray = new Float32Array(analyser.fftSize);

            const recordChunk = () => {
                if (sampleIndex >= totalSamples || this.state !== 'receiving') {
                    // Process recorded audio
                    this.processReceivedAudio(recordedData.slice(0, sampleIndex));
                    return;
                }

                analyser.getFloatTimeDomainData(dataArray);
                const chunkSize = Math.min(dataArray.length, totalSamples - sampleIndex);
                recordedData.set(dataArray.slice(0, chunkSize), sampleIndex);
                sampleIndex += chunkSize;

                // Update signal level
                if (this.onProgress) {
                    const rms = Math.sqrt(dataArray.reduce((sum, x) => sum + x * x, 0) / dataArray.length);
                    const levelDb = 20 * Math.log10(rms + 1e-10);
                    this.onProgress({ levelDb, progress: sampleIndex / totalSamples });
                }

                requestAnimationFrame(recordChunk);
            };

            recordChunk();

        } catch (error) {
            this.state = 'idle';
            this.log('Error: ' + error.message, 'ERROR');
            if (this.onError) {
                this.onError(error);
            }
        }
    }

    /**
     * Process received audio data.
     */
    processReceivedAudio(audioData) {
        this.log('Processing received audio...');
        
        // Try to demodulate
        const { symbols, confidences } = this.demodulator.demodulateSymbols(audioData);
        
        if (symbols.length === 0) {
            this.log('No valid symbols detected');
            return;
        }

        // Convert symbols to bytes
        const bytes = this.demodulator.symbolsToBytes(symbols);
        
        // Try to parse frames
        const receivedFrames = [];
        let offset = 0;

        while (offset < bytes.length - 22) {
            const frame = Frame.deserialize(bytes.slice(offset));
            if (frame) {
                receivedFrames.push(frame);
                offset += 20 + frame.payload.length + 2; // header + payload + CRC
            } else {
                offset++;
            }
        }

        this.log(`Received ${receivedFrames.length} frames`);

        // Find metadata and data frames
        let metadata = null;
        const dataFrames = new Map();

        for (const frame of receivedFrames) {
            if (frame.type === FrameType.METADATA) {
                metadata = decodeMetadata(frame.payload);
                this.log(`File: ${metadata.filename}, Size: ${metadata.filesize}`);
                
                if (this.onProgress) {
                    this.onProgress({
                        type: 'metadata',
                        filename: metadata.filename,
                        filesize: metadata.filesize
                    });
                }
            } else if (frame.type === FrameType.DATA) {
                dataFrames.set(frame.sequenceNumber, frame);
            }
        }

        if (!metadata) {
            this.log('No metadata received');
            return;
        }

        // Reconstruct file
        this.log('Reconstructing file...');
        const reconstructed = [];
        
        for (let i = 0; i < metadata.totalChunks; i++) {
            const frame = dataFrames.get(i);
            if (frame) {
                // Apply FEC decode
                const decoded = this.fec.decode(frame.payload);
                reconstructed.push(decoded.data);
            }
        }

        // Concatenate all chunks
        const totalLength = reconstructed.reduce((sum, chunk) => sum + chunk.length, 0);
        const fileData = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of reconstructed) {
            fileData.set(chunk, offset);
            offset += chunk.length;
        }

        // Verify hash
        this.computeHash(fileData).then(hash => {
            const verified = hash === metadata.fileHash;
            this.log(`Hash verified: ${verified}`);

            if (this.onComplete) {
                this.onComplete({
                    success: verified,
                    filename: metadata.filename,
                    hash: hash,
                    filesize: fileData.length
                });
            }

            // Download file
            if (verified) {
                this.downloadFile(fileData, metadata.filename);
            }
        });
    }

    /**
     * Download file to user's computer.
     */
    downloadFile(data, filename) {
        const blob = new Blob([data]);
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    /**
     * Cancel transfer.
     */
    cancel() {
        this.state = 'idle';
        this.log('Transfer cancelled');
    }

    /**
     * Log message.
     */
    log(message, level = 'INFO') {
        console.log(`[${level}] ${message}`);
        if (this.onLog) {
            this.onLog({ message, level });
        }
    }
}

// Export for use in other modules
window.BrowserTransferManager = BrowserTransferManager;
