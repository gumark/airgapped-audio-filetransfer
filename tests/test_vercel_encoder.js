"use strict";

const assert = require("assert");
const encoder = require("../api/encode-frame.js")._test;

const config = encoder.validateConfig({
  sampleRate: 48000,
  symbolRate: 500,
  frequencies: [1200, 1600, 2000, 2400],
  fecEnabled: false,
  fecOverhead: 0,
});
const metadata = encoder.validateMetadata({
  filename: "hello.txt",
  mimeType: "text/plain",
  filesize: 3,
  chunkSize: 128,
  totalChunks: 1,
  fileHash: "a".repeat(64),
}, config);
const parts = { config, metadata, transferId: 1234, totalFrames: 5 };

const start = encoder.encodeStart(parts);
assert.strictEqual(start.toString("ascii", 0, 4), "RIFF");
assert.strictEqual(start.toString("ascii", 8, 12), "WAVE");
assert.strictEqual(start.readUInt32LE(24), config.sampleRate);

const data = encoder.encodeData(parts, {
  chunk: Buffer.from("abc").toString("base64"),
  sequenceNumber: 0,
});
assert.strictEqual(data.toString("ascii", 0, 4), "RIFF");
assert.ok(data.length > 44);

const end = encoder.encodeEnd(parts);
assert.strictEqual(end.toString("ascii", 0, 4), "RIFF");
assert.strictEqual(encoder.crc16(Buffer.from("ATFR")), 0xc33b);

for (const symbolRate of [150, 250, 500]) {
  for (const fecOverhead of [0, 0.1, 0.4]) {
    const fecEnabled = fecOverhead > 0;
    const testConfig = encoder.validateConfig({
      sampleRate: 48000,
      symbolRate,
      frequencies: [1200, 1600, 2000, 2400],
      fecEnabled,
      fecOverhead,
      fecAlgorithm: fecEnabled ? "xor-parity" : "none",
    });
    const testMetadata = encoder.validateMetadata({
      filename: "batch.bin",
      filesize: 200000,
      chunkSize: 128,
      totalChunks: Math.ceil(200000 / 128),
      fileHash: "b".repeat(64),
    }, testConfig);
    const batchChunks = Math.max(1, Math.floor(
      (8 * symbolRate / 250) /
      (fecEnabled ? 1 + Math.floor(255 * fecOverhead) / 128 : 1)
    ));
    const batch = Buffer.alloc(batchChunks * 128, 7);
    const response = encoder.encodeData(
      { config: testConfig, metadata: testMetadata, transferId: 7, totalFrames: testMetadata.totalChunks + 4 },
      { chunk: batch.toString("base64"), sequenceNumber: 0 },
    );
    assert.ok(response.length <= 4 * 1024 * 1024);
  }
}

console.log("Vercel encoder smoke test passed");
