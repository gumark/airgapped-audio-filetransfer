"use strict";

const MAGIC = Buffer.from([0x41, 0x54, 0x46, 0x52]);
const PROTOCOL_VERSION = 2;
const MAX_PAYLOAD_SIZE = 2048;
const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const FRAME_TYPES = {
  SYNC: 0x01,
  HANDSHAKE: 0x02,
  METADATA: 0x03,
  DATA: 0x04,
  END: 0x06,
};
const FEC_ALGORITHMS = {
  none: 0,
  "reed-solomon": 1,
  "xor-parity": 2,
};

function badRequest(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function integer(value, name, min, max) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw badRequest(`${name} must be an integer between ${min} and ${max}`);
  }
  return parsed;
}

function number(value, name, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
    throw badRequest(`${name} must be between ${min} and ${max}`);
  }
  return parsed;
}

function validateConfig(input = {}) {
  const sampleRate = integer(input.sampleRate ?? 48000, "sampleRate", 8000, 192000);
  const symbolRate = integer(input.symbolRate ?? 250, "symbolRate", 50, 2000);
  const frequencies = input.frequencies || [1200, 1600, 2000, 2400];
  if (!Array.isArray(frequencies) || frequencies.length !== 4 ||
      frequencies.some(f => !Number.isInteger(f) || f <= 0 || f >= sampleRate / 2) ||
      new Set(frequencies).size !== frequencies.length) {
    throw badRequest("frequencies must contain four unique values below Nyquist");
  }
  if (sampleRate / symbolRate < 2) {
    throw badRequest("sampleRate must provide at least two samples per symbol");
  }
  const samplesPerSymbol = Math.floor(sampleRate / symbolRate);
  const detectorBins = frequencies.map(frequency =>
    Math.floor(0.5 + samplesPerSymbol * frequency / sampleRate));
  if (new Set(detectorBins).size !== detectorBins.length) {
    throw badRequest("frequencies must map to unique symbol detector bins");
  }
  const fecEnabled = input.fecEnabled === true;
  const fecOverhead = number(input.fecOverhead ?? 0, "fecOverhead", 0, 0.4);
  if (fecEnabled && fecOverhead <= 0) {
    throw badRequest("fecOverhead must be positive when FEC is enabled");
  }
  if (fecEnabled && input.fecAlgorithm !== "xor-parity") {
    throw badRequest("Vercel encoding supports xor-parity FEC only");
  }
  return {
    sampleRate,
    symbolRate,
    frequencies,
    fecEnabled,
    fecOverhead: fecEnabled ? fecOverhead : 0,
    fecAlgorithm: fecEnabled ? "xor-parity" : "none",
    syncPreambleSymbols: integer(input.syncPreambleSymbols ?? 32, "syncPreambleSymbols", 1, 255),
    syncFrequency: integer(input.syncFrequency ?? 1000, "syncFrequency", 1, sampleRate / 2 - 1),
  };
}

function validateMetadata(input = {}, config) {
  const filename = typeof input.filename === "string"
    ? (input.filename.split(/[\\\\/]/).pop() || "upload.bin")
    : "upload.bin";
  const mimeType = typeof input.mimeType === "string" && input.mimeType
    ? input.mimeType.slice(0, 255) : "application/octet-stream";
  const filesize = integer(input.filesize, "metadata.filesize", 0, Number.MAX_SAFE_INTEGER);
  const chunkSize = integer(input.chunkSize ?? 128, "metadata.chunkSize", 1, 128);
  const totalChunks = integer(input.totalChunks, "metadata.totalChunks", 1, 0xFFFFFFFF);
  const fileHash = typeof input.fileHash === "string" && /^[0-9a-f]{64}$/i.test(input.fileHash)
    ? input.fileHash.toLowerCase() : null;
  if (!fileHash) throw badRequest("metadata.fileHash must be a SHA-256 hex digest");
  const expectedChunks = Math.max(1, Math.ceil(filesize / chunkSize));
  if (totalChunks !== expectedChunks) {
    throw badRequest("metadata.totalChunks does not match metadata.filesize");
  }
  return {
    filename: filename.slice(0, 255),
    mimeType,
    filesize,
    chunkSize,
    totalChunks,
    hashAlgorithm: "sha256",
    fileHash,
    compressionEnabled: false,
    encryptionEnabled: false,
    fecOverhead: config.fecOverhead,
    fecEnabled: config.fecEnabled,
    fecAlgorithm: config.fecAlgorithm,
  };
}

function frame(type, transferId, sequenceNumber, totalFrames, payload = Buffer.alloc(0)) {
  if (payload.length > MAX_PAYLOAD_SIZE) throw badRequest("frame payload exceeds 2048 bytes");
  const header = Buffer.alloc(20);
  MAGIC.copy(header, 0);
  header[4] = PROTOCOL_VERSION;
  header.writeUInt32BE(transferId >>> 0, 5);
  header[9] = type;
  header.writeUInt32BE(sequenceNumber >>> 0, 10);
  header.writeUInt32BE(totalFrames >>> 0, 14);
  header.writeUInt16BE(payload.length, 18);
  const body = Buffer.concat([header, payload]);
  const crc = crc16(body);
  const crcBuffer = Buffer.alloc(2);
  crcBuffer.writeUInt16BE(crc);
  return Buffer.concat([body, crcBuffer]);
}

function crc16(data) {
  let crc = 0xFFFF;
  for (const byte of data) {
    crc ^= byte << 8;
    for (let bit = 0; bit < 8; bit++) {
      crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
      crc &= 0xFFFF;
    }
  }
  return crc;
}

function encodeHandshake(config) {
  const payload = Buffer.alloc(17 + config.frequencies.length * 2);
  payload.writeUInt32BE(config.sampleRate, 0);
  payload.writeUInt32BE(config.symbolRate, 4);
  payload[8] = 2;
  payload[9] = config.frequencies.length;
  payload[10] = Math.round(config.fecOverhead * 100);
  payload[11] = FEC_ALGORITHMS[config.fecAlgorithm];
  payload[12] = config.fecEnabled ? 1 : 0;
  payload.writeUInt16BE(config.syncPreambleSymbols, 13);
  payload.writeUInt16BE(config.syncFrequency, 15);
  config.frequencies.forEach((frequency, index) => payload.writeUInt16BE(frequency, 17 + index * 2));
  return payload;
}

function lengthPrefixed(value) {
  const bytes = Buffer.from(value, "utf8");
  if (bytes.length > 0xFFFF) throw badRequest("metadata field is too long");
  const length = Buffer.alloc(2);
  length.writeUInt16BE(bytes.length);
  return Buffer.concat([length, bytes]);
}

function encodeMetadata(metadata) {
  const fields = [metadata.filename, metadata.mimeType, metadata.hashAlgorithm, metadata.fileHash]
    .map(lengthPrefixed);
  const prefix = Buffer.alloc(17);
  prefix.writeBigUInt64BE(BigInt(metadata.filesize), 0);
  prefix.writeUInt32BE(metadata.chunkSize, 8);
  prefix.writeUInt32BE(metadata.totalChunks, 12);
  prefix[16] = fields.length;
  const flags = Buffer.from([
    metadata.compressionEnabled ? 1 : 0,
    metadata.encryptionEnabled ? 1 : 0,
    Math.round(metadata.fecOverhead * 100),
    metadata.fecEnabled ? 1 : 0,
    FEC_ALGORITHMS[metadata.fecAlgorithm],
  ]);
  return Buffer.concat([prefix, ...fields, flags]);
}

function xorParity(data, overhead) {
  if (!overhead) return Buffer.from(data);
  const paritySize = Math.max(1, Math.floor(255 * overhead));
  const encoded = Buffer.alloc(data.length + paritySize);
  Buffer.from(data).copy(encoded);
  let parity = 0;
  for (const byte of data) parity ^= byte;
  encoded.fill(parity, data.length);
  return encoded;
}

function bytesToSymbols(data) {
  const symbols = [];
  let accumulator = 0;
  let bits = 0;
  for (const byte of data) {
    accumulator = (accumulator << 8) | byte;
    bits += 8;
    while (bits >= 2) {
      bits -= 2;
      symbols.push((accumulator >> bits) & 3);
      accumulator &= bits ? (1 << bits) - 1 : 0;
    }
  }
  if (bits) symbols.push((accumulator << (2 - bits)) & 3);
  return symbols;
}

function modulateSymbols(symbols, config) {
  const samplesPerSymbol = Math.floor(config.sampleRate / config.symbolRate);
  const waveform = new Float32Array(symbols.length * samplesPerSymbol);
  let phase = 0;
  for (let index = 0; index < symbols.length; index++) {
    const frequency = config.frequencies[symbols[index]];
    const omega = 2 * Math.PI * frequency / config.sampleRate;
    const start = index * samplesPerSymbol;
    for (let sample = 0; sample < samplesPerSymbol; sample++) {
      waveform[start + sample] = 0.8 * Math.sin(phase + omega * sample);
    }
    phase = (phase + omega * samplesPerSymbol) % (2 * Math.PI);
  }
  return waveform;
}

function syncTone(config, duration = 0.5) {
  const count = Math.floor(config.sampleRate * duration);
  const tone = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    tone[i] = 0.4 * Math.sin(2 * Math.PI * config.syncFrequency * i / config.sampleRate);
  }
  return tone;
}

function concatAudio(parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const audio = new Float32Array(total);
  let offset = 0;
  for (const part of parts) {
    audio.set(part, offset);
    offset += part.length;
  }
  return audio;
}

function wav(audio, sampleRate) {
  const result = Buffer.alloc(44 + audio.length * 2);
  result.write("RIFF", 0);
  result.writeUInt32LE(36 + audio.length * 2, 4);
  result.write("WAVE", 8);
  result.write("fmt ", 12);
  result.writeUInt32LE(16, 16);
  result.writeUInt16LE(1, 20);
  result.writeUInt16LE(1, 22);
  result.writeUInt32LE(sampleRate, 24);
  result.writeUInt32LE(sampleRate * 2, 28);
  result.writeUInt16LE(2, 32);
  result.writeUInt16LE(16, 34);
  result.write("data", 36);
  result.writeUInt32LE(audio.length * 2, 40);
  for (let i = 0; i < audio.length; i++) {
    const sample = Math.max(-1, Math.min(1, audio[i]));
    result.writeInt16LE(Math.round(sample * 32767), 44 + i * 2);
  }
  return result;
}

function decodeBody(req) {
  if (req.body && typeof req.body === "object") return Promise.resolve(req.body);
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", chunk => {
      body += chunk;
      if (body.length > 1024 * 1024) reject(badRequest("request body is too large"));
    });
    req.on("end", () => {
      try { resolve(JSON.parse(body || "{}")); }
      catch (_) { reject(badRequest("request body must be valid JSON")); }
    });
    req.on("error", reject);
  });
}

function requestParts(body) {
  const config = validateConfig(body.config);
  const metadata = validateMetadata(body.metadata, config);
  const transferId = integer(body.transferId || 1, "transferId", 1, 0xFFFFFFFF);
  const totalFrames = metadata.totalChunks + 4;
  if (body.totalFrames !== undefined && Number(body.totalFrames) !== totalFrames) {
    throw badRequest("totalFrames does not match metadata");
  }
  return { config, metadata, transferId, totalFrames };
}

function encodeFrames(frames, config, includePrefix = false) {
  const audio = [];
  if (includePrefix) {
    audio.push(syncTone(config));
    audio.push(modulateSymbols(
      Array.from({ length: config.syncPreambleSymbols }, (_, index) => index % 2), config,
    ));
  }
  for (const data of frames) audio.push(modulateSymbols(bytesToSymbols(data), config));
  return wav(concatAudio(audio), config.sampleRate);
}

function encodeStart(parts) {
  const { config, metadata, transferId, totalFrames } = parts;
  return encodeFrames([
    frame(FRAME_TYPES.SYNC, transferId, 0, totalFrames),
    frame(FRAME_TYPES.HANDSHAKE, transferId, 0, totalFrames, encodeHandshake(config)),
    frame(FRAME_TYPES.METADATA, transferId, 0, totalFrames, encodeMetadata(metadata)),
  ], config, true);
}

function encodeData(parts, body) {
  const { config, metadata, transferId, totalFrames } = parts;
  if (typeof body.chunk !== "string") throw badRequest("chunk must be base64 text");
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(body.chunk) || body.chunk.length % 4 === 1) {
    throw badRequest("chunk must be valid base64 text");
  }
  const raw = Buffer.from(body.chunk, "base64");
  if (!raw.length && metadata.filesize > 0) throw badRequest("chunk is empty");
  if (raw.length > 16 * 1024) throw badRequest("chunk batch is too large");
  const sequenceNumber = integer(body.sequenceNumber, "sequenceNumber", 0, metadata.totalChunks - 1);
  const firstByte = sequenceNumber * metadata.chunkSize;
  const remainingBytes = metadata.filesize - firstByte;
  if (raw.length > remainingBytes) throw badRequest("chunk batch exceeds metadata.filesize");
  const frameCount = Math.max(1, Math.ceil(raw.length / metadata.chunkSize));
  const isFinalBatch = firstByte + raw.length === metadata.filesize;
  if (raw.length % metadata.chunkSize !== 0 && !isFinalBatch) {
    throw badRequest("only the final data batch may contain a partial chunk");
  }
  if (sequenceNumber + frameCount > metadata.totalChunks) {
    throw badRequest("data batch exceeds total chunks");
  }
  const frames = [];
  for (let offset = 0; offset < raw.length || (raw.length === 0 && !frames.length); offset += metadata.chunkSize) {
    const chunk = raw.subarray(offset, offset + metadata.chunkSize);
    const sequence = sequenceNumber + frames.length;
    if (sequence >= metadata.totalChunks) throw badRequest("data batch exceeds total chunks");
    const payload = xorParity(chunk, config.fecOverhead);
    frames.push(frame(FRAME_TYPES.DATA, transferId, sequence, totalFrames, payload));
  }
  return encodeFrames(frames, config);
}

function encodeEnd(parts) {
  const { config, transferId, totalFrames } = parts;
  return encodeFrames([frame(FRAME_TYPES.END, transferId, 0, totalFrames)], config);
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({ error: "Method not allowed" });
    return;
  }
  try {
    const body = await decodeBody(req);
    const parts = requestParts(body);
    let audio;
    if (body.action === "start") audio = encodeStart(parts);
    else if (body.action === "data") audio = encodeData(parts, body);
    else if (body.action === "end") audio = encodeEnd(parts);
    else throw badRequest("action must be start, data, or end");

    if (audio.length > MAX_RESPONSE_BYTES) {
      throw badRequest("encoded audio batch exceeds the server response limit");
    }
    res.setHeader("Content-Type", "audio/wav");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Length", audio.length);
    res.status(200).send(audio);
  } catch (error) {
    const status = error.statusCode || 500;
    res.status(status).json({ error: status === 500 ? "Audio encoding failed" : error.message });
  }
};

module.exports._test = {
  crc16,
  encodeMetadata,
  encodeHandshake,
  encodeStart,
  encodeData,
  encodeEnd,
  frame,
  wav,
  validateConfig,
  validateMetadata,
};
