/**
 * Browser-based Forward Error Correction.
 * 
 * Implements a simple parity-based FEC for browser deployment.
 * For full Reed-Solomon, use the Python backend.
 */

class SimpleFEC {
    constructor(overheadPercent = 0) {
        // Browser parity is detection-only, not Reed-Solomon correction. Keep
        // it disabled by default so the UI never promises correction it cannot
        // provide. The Python backend remains the full-FEC implementation.
        this.overheadPercent = Math.max(0, Math.min(100, overheadPercent));
        this.eccSize = this.overheadPercent === 0
            ? 0
            : Math.max(1, Math.floor(255 * (this.overheadPercent / 100)));
        this.dataSize = 255 - this.eccSize;
    }

    /**
     * Encode data with parity bytes.
     * Uses XOR-based parity for simplicity.
     */
    encode(data) {
        if (this.eccSize === 0) return new Uint8Array(data);
        const encoded = new Uint8Array(data.length + this.eccSize);
        encoded.set(data);

        // Generate parity bytes
        for (let i = 0; i < this.eccSize; i++) {
            let parity = 0;
            for (let j = 0; j < data.length; j++) {
                parity ^= data[j];
            }
            encoded[data.length + i] = parity;
        }

        return encoded;
    }

    /**
     * Decode data, attempting to correct errors.
     */
    decode(encoded) {
        if (this.eccSize === 0) {
            return { data: new Uint8Array(encoded), corrected: false, errors: 0, valid: true };
        }
        if (encoded.length < this.eccSize) {
            return { data: encoded, corrected: false, errors: 0, valid: false };
        }

        const dataEnd = encoded.length - this.eccSize;
        const data = encoded.slice(0, dataEnd);
        const parity = encoded.slice(dataEnd);

        // Verify parity
        let errors = 0;
        for (let i = 0; i < this.eccSize; i++) {
            let expectedParity = 0;
            for (let j = 0; j < data.length; j++) {
                expectedParity ^= data[j];
            }
            if (expectedParity !== parity[i]) {
                errors++;
            }
        }

        return {
            data: data,
            corrected: false,
            errors: errors,
            valid: errors === 0
        };
    }

    /**
     * Encode data in chunks.
     */
    encodeChunks(data, chunkSize = 128) {
        const chunks = [];
        for (let i = 0; i < data.length; i += chunkSize) {
            const chunk = data.slice(i, i + chunkSize);
            chunks.push(this.encode(chunk));
        }
        return chunks;
    }

    /**
     * Decode multiple chunks.
     */
    decodeChunks(chunks) {
        let totalErrors = 0;
        const decoded = [];

        for (const chunk of chunks) {
            const result = this.decode(chunk);
            if (!result.valid) {
                return { data: new Uint8Array(0), totalErrors: totalErrors + result.errors, valid: false };
            }
            decoded.push(result.data);
            totalErrors += result.errors;
        }

        // Concatenate all decoded data
        const totalLength = decoded.reduce((sum, d) => sum + d.length, 0);
        const result = new Uint8Array(totalLength);
        let offset = 0;
        for (const d of decoded) {
            result.set(d, offset);
            offset += d.length;
        }

        return { data: result, totalErrors, valid: true };
    }
}

// Export for use in other modules
window.SimpleFEC = SimpleFEC;
