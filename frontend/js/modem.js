/**
 * Browser-based FSK Modem using Web Audio API.
 * 
 * Handles modulation (bytes → audio) and demodulation (audio → bytes)
 * entirely in the browser using the Web Audio API.
 */

class FSKModulator {
    constructor(sampleRate = 48000, symbolRate = 250, frequencies = [1200, 1600, 2000, 2400]) {
        this.sampleRate = sampleRate;
        this.symbolRate = symbolRate;
        this.frequencies = frequencies;
        this.bitsPerSymbol = Math.log2(frequencies.length);
        this.samplesPerSymbol = Math.floor(sampleRate / symbolRate);
    }

    /**
     * Convert bytes to symbols (MSB first).
     */
    bytesToSymbols(data) {
        const mask = (1 << this.bitsPerSymbol) - 1;
        const symbolsPerByte = 8 / this.bitsPerSymbol;
        const symbols = [];

        for (let i = 0; i < data.length; i++) {
            const byte = data[i];
            for (let j = 0; j < symbolsPerByte; j++) {
                const shift = 8 - this.bitsPerSymbol * (j + 1);
                symbols.push((byte >> shift) & mask);
            }
        }
        return symbols;
    }

    /**
     * Convert symbols back to bytes.
     */
    symbolsToBytes(symbols) {
        const bytes = [];
        let currentByte = 0;
        let bitsInByte = 0;

        for (const symbol of symbols) {
            currentByte = (currentByte << this.bitsPerSymbol) | symbol;
            bitsInByte += this.bitsPerSymbol;

            if (bitsInByte >= 8) {
                bytes.push(currentByte >> (bitsInByte - 8));
                bitsInByte -= 8;
                currentByte = currentByte & ((1 << bitsInByte) - 1);
            }
        }

        // Pad remaining bits
        if (bitsInByte > 0) {
            currentByte <<= (8 - bitsInByte);
            bytes.push(currentByte);
        }

        return new Uint8Array(bytes);
    }

    /**
     * Modulate symbols into an AudioBuffer.
     */
    modulateSymbols(symbols) {
        const totalSamples = symbols.length * this.samplesPerSymbol;
        const buffer = new Float32Array(totalSamples);
        let phase = 0;

        for (let i = 0; i < symbols.length; i++) {
            const freq = this.frequencies[symbols[i]];
            const omega = (2 * Math.PI * freq) / this.sampleRate;
            const start = i * this.samplesPerSymbol;

            for (let j = 0; j < this.samplesPerSymbol; j++) {
                buffer[start + j] = 0.8 * Math.sin(phase + omega * j);
            }

            phase = (phase + omega * this.samplesPerSymbol) % (2 * Math.PI);
        }

        return buffer;
    }

    /**
     * Modulate bytes directly into an AudioBuffer.
     */
    modulateBytes(data) {
        const symbols = this.bytesToSymbols(data instanceof Uint8Array ? data : new Uint8Array(data));
        return this.modulateSymbols(symbols);
    }

    /**
     * Add a sync tone before the data.
     */
    addSyncTone(audioBuffer, duration = 0.5, frequency = 1000) {
        const toneSamples = Math.floor(this.sampleRate * duration);
        const totalSamples = toneSamples + audioBuffer.length;
        const result = new Float32Array(totalSamples);

        // Generate sync tone
        for (let i = 0; i < toneSamples; i++) {
            result[i] = 0.5 * Math.sin((2 * Math.PI * frequency * i) / this.sampleRate);
        }

        // Copy data
        result.set(audioBuffer, toneSamples);
        return result;
    }
}

class FSKDemodulator {
    constructor(sampleRate = 48000, symbolRate = 250, frequencies = [1200, 1600, 2000, 2400]) {
        this.sampleRate = sampleRate;
        this.symbolRate = symbolRate;
        this.frequencies = frequencies;
        this.bitsPerSymbol = Math.log2(frequencies.length);
        this.samplesPerSymbol = Math.floor(sampleRate / symbolRate);
    }

    /**
     * Goertzel algorithm for single-frequency detection.
     */
    goertzel(samples, targetFreq) {
        const N = samples.length;
        const k = Math.round((N * targetFreq) / this.sampleRate);
        const omega = (2 * Math.PI * k) / N;
        const cosine = Math.cos(omega);
        const coeff = 2 * cosine;

        let sPrev = 0;
        let sPrev2 = 0;

        for (let i = 0; i < N; i++) {
            const s = samples[i] + coeff * sPrev - sPrev2;
            sPrev2 = sPrev;
            sPrev = s;
        }

        return sPrev2 * sPrev2 + sPrev * sPrev - coeff * sPrev * sPrev2;
    }

    /**
     * Detect a single symbol from audio samples.
     */
    detectSymbol(samples) {
        const powers = this.frequencies.map(f => this.goertzel(samples, f));
        const maxPower = Math.max(...powers);
        const detected = powers.indexOf(maxPower);
        const sorted = [...powers].sort((a, b) => b - a);
        const confidence = sorted[0] / (sorted[1] || 1e-10);

        return { symbol: detected, confidence, powers };
    }

    /**
     * Demodulate audio buffer into symbols.
     */
    demodulateSymbols(audioBuffer, offset = 0) {
        const symbols = [];
        const confidences = [];
        const numSymbols = Math.floor((audioBuffer.length - offset) / this.samplesPerSymbol);

        for (let i = 0; i < numSymbols; i++) {
            const start = offset + i * this.samplesPerSymbol;
            const end = start + this.samplesPerSymbol;
            const chunk = audioBuffer.slice(start, end);

            // Apply Hamming window
            const windowed = new Float32Array(chunk.length);
            for (let j = 0; j < chunk.length; j++) {
                windowed[j] = chunk[j] * (0.54 - 0.46 * Math.cos((2 * Math.PI * j) / (chunk.length - 1)));
            }

            const { symbol, confidence } = this.detectSymbol(windowed);
            symbols.push(symbol);
            confidences.push(confidence);
        }

        return { symbols, confidences };
    }

    /**
     * Demodulate audio buffer directly to bytes.
     */
    demodulateToBytes(audioBuffer, offset = 0) {
        const { symbols } = this.demodulateSymbols(audioBuffer, offset);
        return this.symbolsToBytes(symbols);
    }

    /**
     * Measure SNR of the audio.
     */
    measureSNR(audioBuffer, signalFreq = 1500) {
        const signalPower = this.goertzel(audioBuffer, signalFreq);
        const noiseFreqs = [300, 500, 700];
        const noisePower = noiseFreqs.reduce((sum, f) => sum + this.goertzel(audioBuffer, f), 0) / noiseFreqs.length;

        return 10 * Math.log10(signalPower / (noisePower + 1e-10));
    }
}

// Export for use in other modules
window.FSKModulator = FSKModulator;
window.FSKDemodulator = FSKDemodulator;
