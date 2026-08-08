/**
 * Browser-based Protocol Handling.
 * 
 * Implements packet structure and serialization for the audio transfer protocol.
 */

const MAGIC = new Uint8Array([0x41, 0x54, 0x46, 0x52]); // "ATFR"
const PROTOCOL_VERSION = 2;

const FEC_ALGORITHM = {
    NONE: 0,
    REED_SOLOMON: 1,
    XOR_PARITY: 2
};

const FEC_ALGORITHM_NAMES = {
    0: 'none',
    1: 'reed-solomon',
    2: 'xor-parity'
};

const FrameType = {
    SYNC: 0x01,
    HANDSHAKE: 0x02,
    METADATA: 0x03,
    DATA: 0x04,
    PARITY: 0x05,
    END: 0x06,
    ACK: 0x07,
    ERROR: 0x08,
    CALIBRATION: 0x09
};

class Frame {
    constructor(type, transferId = 0, sequenceNumber = 0, totalFrames = 0, payload = new Uint8Array(0)) {
        this.type = type;
        this.transferId = transferId || Math.floor(Math.random() * 0xFFFFFFFF);
        this.sequenceNumber = sequenceNumber;
        this.totalFrames = totalFrames;
        this.payload = payload;
    }

    /**
     * Calculate CRC-16/CCITT.
     */
    static calculateCRC(data) {
        let crc = 0xFFFF;
        for (let i = 0; i < data.length; i++) {
            crc ^= data[i] << 8;
            for (let j = 0; j < 8; j++) {
                if (crc & 0x8000) {
                    crc = (crc << 1) ^ 0x1021;
                } else {
                    crc <<= 1;
                }
                crc &= 0xFFFF;
            }
        }
        return crc;
    }

    /**
     * Serialize frame to bytes.
     */
    serialize() {
        // Header: MAGIC(4) + VERSION(1) + TRANSFER_ID(4) + TYPE(1) + SEQ_NUM(4) + TOTAL_FRAMES(4) + PAYLOAD_LEN(2)
        const header = new Uint8Array(20);
        header.set(MAGIC, 0);
        header[4] = PROTOCOL_VERSION;
        
        // Transfer ID (big-endian)
        header[5] = (this.transferId >> 24) & 0xFF;
        header[6] = (this.transferId >> 16) & 0xFF;
        header[7] = (this.transferId >> 8) & 0xFF;
        header[8] = this.transferId & 0xFF;
        
        header[9] = this.type;
        
        // Sequence number (big-endian)
        header[10] = (this.sequenceNumber >> 24) & 0xFF;
        header[11] = (this.sequenceNumber >> 16) & 0xFF;
        header[12] = (this.sequenceNumber >> 8) & 0xFF;
        header[13] = this.sequenceNumber & 0xFF;
        
        // Total frames (big-endian)
        header[14] = (this.totalFrames >> 24) & 0xFF;
        header[15] = (this.totalFrames >> 16) & 0xFF;
        header[16] = (this.totalFrames >> 8) & 0xFF;
        header[17] = this.totalFrames & 0xFF;
        
        // Payload length (big-endian)
        header[18] = (this.payload.length >> 8) & 0xFF;
        header[19] = this.payload.length & 0xFF;

        // Combine header and payload
        const data = new Uint8Array(header.length + this.payload.length);
        data.set(header, 0);
        data.set(this.payload, header.length);

        // Calculate CRC
        const crc = Frame.calculateCRC(data);
        const crcBytes = new Uint8Array(2);
        crcBytes[0] = (crc >> 8) & 0xFF;
        crcBytes[1] = crc & 0xFF;

        // Final frame with CRC
        const frame = new Uint8Array(data.length + 2);
        frame.set(data, 0);
        frame.set(crcBytes, data.length);

        return frame;
    }

    /**
     * Deserialize bytes to Frame.
     */
    static deserialize(data) {
        if (data.length < 22) return null; // Minimum frame size

        // Verify magic
        for (let i = 0; i < 4; i++) {
            if (data[i] !== MAGIC[i]) return null;
        }

        // Verify version
        if (data[4] !== PROTOCOL_VERSION) return null;

        // Extract fields
        const transferId = (data[5] << 24) | (data[6] << 16) | (data[7] << 8) | data[8];
        const type = data[9];
        if (!Object.values(FrameType).includes(type)) return null;
        const sequenceNumber = (data[10] << 24) | (data[11] << 16) | (data[12] << 8) | data[13];
        const totalFrames = (data[14] << 24) | (data[15] << 16) | (data[16] << 8) | data[17];
        const payloadLength = (data[18] << 8) | data[19];
        if (payloadLength > 2048 || data.length < 22 + payloadLength) return null;

        // Verify CRC
        const frameData = data.slice(0, 20 + payloadLength);
        const receivedCRC = (data[20 + payloadLength] << 8) | data[21 + payloadLength];
        const calculatedCRC = Frame.calculateCRC(frameData);

        if (receivedCRC !== calculatedCRC) return null;

        // Extract payload
        const payload = data.slice(20, 20 + payloadLength);

        return new Frame(type, transferId, sequenceNumber, totalFrames, payload);
    }
}

/**
 * Encode metadata into payload bytes.
 */
function encodeHandshake(options = {}) {
    const sampleRate = options.sampleRate || 48000;
    const symbolRate = options.symbolRate || 250;
    const bitsPerSymbol = options.bitsPerSymbol || 2;
    const frequencies = options.frequencies || [1200, 1600, 2000, 2400];
    const fecEnabled = options.fecEnabled !== false;
    const fecAlgorithm = options.fecAlgorithm ||
        (fecEnabled ? 'xor-parity' : 'none');
    const fecAlgorithmId = Object.entries(FEC_ALGORITHM_NAMES)
        .find(([, name]) => name === fecAlgorithm)?.[0];
    if (fecAlgorithmId === undefined) throw new RangeError('unsupported FEC algorithm');
    const preambleSymbols = options.syncPreambleSymbols || 32;
    const syncFrequency = options.syncFrequency || 1000;
    const headerSize = 4 + 4 + 1 + 1 + 1 + 1 + 1 + 2 + 2;
    const payload = new Uint8Array(headerSize + frequencies.length * 2);
    const view = new DataView(payload.buffer);
    let offset = 0;
    view.setUint32(offset, sampleRate); offset += 4;
    view.setUint32(offset, symbolRate); offset += 4;
    payload[offset++] = bitsPerSymbol;
    payload[offset++] = frequencies.length;
    payload[offset++] = Math.round((options.fecOverhead || 0) * 100);
    payload[offset++] = Number(fecAlgorithmId);
    payload[offset++] = fecEnabled ? 1 : 0;
    view.setUint16(offset, preambleSymbols); offset += 2;
    view.setUint16(offset, syncFrequency); offset += 2;
    for (const frequency of frequencies) {
        view.setUint16(offset, frequency);
        offset += 2;
    }
    return payload;
}

/**
 * Encode metadata into payload bytes.
 */
function decodeHandshake(payload) {
    if (!(payload instanceof Uint8Array) || payload.length < 17) {
        throw new RangeError('handshake payload is truncated');
    }
    const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
    let offset = 0;
    const sampleRate = view.getUint32(offset); offset += 4;
    const symbolRate = view.getUint32(offset); offset += 4;
    const bitsPerSymbol = payload[offset++];
    const frequencyCount = payload[offset++];
    const fecOverhead = payload[offset++] / 100;
    const fecAlgorithm = FEC_ALGORITHM_NAMES[payload[offset++]];
    const fecEnabled = payload[offset++] === 1;
    const syncPreambleSymbols = view.getUint16(offset); offset += 2;
    const syncFrequency = view.getUint16(offset); offset += 2;
    if (!fecAlgorithm) throw new RangeError('unknown handshake FEC algorithm');
    if (payload.length !== offset + frequencyCount * 2) {
        throw new RangeError('handshake payload has an invalid length');
    }
    const frequencies = [];
    for (let i = 0; i < frequencyCount; i++) {
        frequencies.push(view.getUint16(offset));
        offset += 2;
    }
    return {
        sampleRate, symbolRate, bitsPerSymbol, frequencies,
        fecOverhead, fecAlgorithm, fecEnabled,
        syncPreambleSymbols, syncFrequency
    };
}

function encodeMetadata(metadata) {
    const encoder = new TextEncoder();
    const fields = [
        encoder.encode(metadata.filename || ''),
        encoder.encode(metadata.mimeType || 'application/octet-stream'),
        encoder.encode(metadata.hashAlgorithm || 'sha256'),
        encoder.encode(metadata.fileHash || '')
    ];

    // Calculate total size
    let totalSize = 8 + 4 + 4 + 1; // filesize(8) + chunkSize(4) + totalChunks(4) + numFields(1)
    for (const field of fields) {
        totalSize += 2 + field.length; // length(2) + data
    }
    totalSize += 5; // compression + encryption + FEC overhead + FEC enabled + algorithm

    const payload = new Uint8Array(totalSize);
    let offset = 0;

    // File size (big-endian 64-bit). Use BigInt so files above 4 GiB
    // are represented correctly without bitwise-number truncation.
    const fileSize = BigInt(metadata.filesize || 0);
    for (let shift = 56n; shift >= 0n; shift -= 8n) {
        payload[offset++] = Number((fileSize >> shift) & 0xFFn);
    }

    // Chunk size
    const chunkSize = metadata.chunkSize || 4096;
    payload[offset++] = (chunkSize >> 24) & 0xFF;
    payload[offset++] = (chunkSize >> 16) & 0xFF;
    payload[offset++] = (chunkSize >> 8) & 0xFF;
    payload[offset++] = chunkSize & 0xFF;

    // Total chunks
    const totalChunks = metadata.totalChunks || 1;
    payload[offset++] = (totalChunks >> 24) & 0xFF;
    payload[offset++] = (totalChunks >> 16) & 0xFF;
    payload[offset++] = (totalChunks >> 8) & 0xFF;
    payload[offset++] = totalChunks & 0xFF;

    // Number of fields
    payload[offset++] = fields.length;

    // Length-prefixed fields
    for (const field of fields) {
        payload[offset++] = (field.length >> 8) & 0xFF;
        payload[offset++] = field.length & 0xFF;
        payload.set(field, offset);
        offset += field.length;
    }

    // Flags and FEC overhead percentage
    payload[offset++] = metadata.compressionEnabled ? 1 : 0;
    payload[offset++] = metadata.encryptionEnabled ? 1 : 0;
    payload[offset++] = Math.max(0, Math.min(100, Math.round((metadata.fecOverhead || 0) * 100)));
    payload[offset++] = metadata.fecEnabled === false ? 0 : 1;
    const fecAlgorithm = metadata.fecAlgorithm ||
        (metadata.fecEnabled === false ? 'none' : 'xor-parity');
    const fecAlgorithmId = Object.entries(FEC_ALGORITHM_NAMES)
        .find(([, name]) => name === fecAlgorithm)?.[0];
    if (fecAlgorithmId === undefined) throw new RangeError('unsupported FEC algorithm');
    payload[offset++] = Number(fecAlgorithmId);

    return payload;
}

/**
 * Decode metadata from payload bytes.
 */
function decodeMetadata(payload) {
    if (!(payload instanceof Uint8Array) || payload.length < 17) {
        throw new RangeError('metadata payload is truncated');
    }
    let offset = 0;

    // File size (64-bit; preserve exact values where supported).
    let filesizeBig = 0n;
    for (let i = 0; i < 8; i++) {
        filesizeBig = (filesizeBig << 8n) | BigInt(payload[offset++]);
    }
    const filesize = filesizeBig <= BigInt(Number.MAX_SAFE_INTEGER)
        ? Number(filesizeBig)
        : filesizeBig.toString();

    // Chunk size
    const chunkSize = (payload[offset] << 24) | (payload[offset + 1] << 16) | (payload[offset + 2] << 8) | payload[offset + 3];
    offset += 4;

    // Total chunks
    const totalChunks = (payload[offset] * 0x1000000) +
        (payload[offset + 1] << 16) +
        (payload[offset + 2] << 8) + payload[offset + 3];
    offset += 4;

    // Number of fields
    const numFields = payload[offset++];
    if (numFields > 4) throw new RangeError('metadata contains too many fields');

    const decoder = new TextDecoder();
    const fieldNames = ['filename', 'mimeType', 'hashAlgorithm', 'fileHash'];
    const result = { filesize, chunkSize, totalChunks };

    for (let i = 0; i < numFields; i++) {
        if (offset + 2 > payload.length) throw new RangeError('metadata field length is truncated');
        const fieldLen = (payload[offset] << 8) | payload[offset + 1];
        offset += 2;
        if (offset + fieldLen > payload.length) throw new RangeError('metadata field is truncated');
        const fieldValue = decoder.decode(payload.slice(offset, offset + fieldLen));
        offset += fieldLen;
        if (i < fieldNames.length) {
            result[fieldNames[i]] = fieldValue;
        }
    }

    // Flags
    if (offset < payload.length) {
        result.compressionEnabled = payload[offset++] === 1;
    }
    if (offset < payload.length) {
        result.encryptionEnabled = payload[offset++] === 1;
    }
    if (offset < payload.length) {
        result.fecOverhead = payload[offset++] / 100;
    }
    if (offset < payload.length) {
        result.fecEnabled = payload[offset++] === 1;
    }
    if (offset < payload.length) {
        result.fecAlgorithm = FEC_ALGORITHM_NAMES[payload[offset++]];
        if (!result.fecAlgorithm) throw new RangeError('unknown FEC algorithm');
    }
    if (offset !== payload.length) throw new RangeError('metadata contains trailing bytes');

    return result;
}

// Export for use in other modules
window.FrameType = FrameType;
window.Frame = Frame;
window.FEC_ALGORITHM = FEC_ALGORITHM;
window.encodeHandshake = encodeHandshake;
window.decodeHandshake = decodeHandshake;
window.encodeMetadata = encodeMetadata;
window.decodeMetadata = decodeMetadata;
