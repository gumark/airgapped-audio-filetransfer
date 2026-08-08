/**
 * Browser-based Protocol Handling.
 * 
 * Implements packet structure and serialization for the audio transfer protocol.
 */

const MAGIC = new Uint8Array([0x41, 0x54, 0x46, 0x52]); // "ATFR"
const PROTOCOL_VERSION = 1;

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
        const sequenceNumber = (data[10] << 24) | (data[11] << 16) | (data[12] << 8) | data[13];
        const totalFrames = (data[14] << 24) | (data[15] << 16) | (data[16] << 8) | data[17];
        const payloadLength = (data[18] << 8) | data[19];

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
function encodeMetadata(metadata) {
    const encoder = new TextEncoder();
    const fields = [
        encoder.encode(metadata.filename || ''),
        encoder.encode(metadata.mimeType || 'application/octet-stream'),
        encoder.encode(metadata.hashAlgorithm || 'sha-256'),
        encoder.encode(metadata.fileHash || '')
    ];

    // Calculate total size
    let totalSize = 8 + 4 + 4 + 1; // filesize(8) + chunkSize(4) + totalChunks(4) + numFields(1)
    for (const field of fields) {
        totalSize += 2 + field.length; // length(2) + data
    }
    totalSize += 2; // compression + encryption flags

    const payload = new Uint8Array(totalSize);
    let offset = 0;

    // File size (big-endian 64-bit, simplified to 32-bit for JS)
    const fileSize = metadata.filesize || 0;
    payload[offset++] = (fileSize >> 24) & 0xFF;
    payload[offset++] = (fileSize >> 16) & 0xFF;
    payload[offset++] = (fileSize >> 8) & 0xFF;
    payload[offset++] = fileSize & 0xFF;
    payload[offset++] = 0; // Upper 32 bits
    payload[offset++] = 0;
    payload[offset++] = 0;
    payload[offset++] = 0;

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

    // Flags
    payload[offset++] = metadata.compressionEnabled ? 1 : 0;
    payload[offset++] = metadata.encryptionEnabled ? 1 : 0;

    return payload;
}

/**
 * Decode metadata from payload bytes.
 */
function decodeMetadata(payload) {
    let offset = 0;

    // File size
    const filesize = (payload[offset] << 24) | (payload[offset + 1] << 16) | (payload[offset + 2] << 8) | payload[offset + 3];
    offset += 8; // Skip upper 32 bits

    // Chunk size
    const chunkSize = (payload[offset] << 24) | (payload[offset + 1] << 16) | (payload[offset + 2] << 8) | payload[offset + 3];
    offset += 4;

    // Total chunks
    const totalChunks = (payload[offset] << 24) | (payload[offset + 1] << 16) | (payload[offset + 2] << 8) | payload[offset + 3];
    offset += 4;

    // Number of fields
    const numFields = payload[offset++];

    const decoder = new TextDecoder();
    const fieldNames = ['filename', 'mimeType', 'hashAlgorithm', 'fileHash'];
    const result = { filesize, chunkSize, totalChunks };

    for (let i = 0; i < numFields; i++) {
        const fieldLen = (payload[offset] << 8) | payload[offset + 1];
        offset += 2;
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

    return result;
}

// Export for use in other modules
window.FrameType = FrameType;
window.Frame = Frame;
window.encodeMetadata = encodeMetadata;
window.decodeMetadata = decodeMetadata;
