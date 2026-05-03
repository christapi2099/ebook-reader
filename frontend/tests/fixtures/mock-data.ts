export const MOCK_SENTENCES = [
  { index: 0, text: 'Sentence 0.', page: 0, x0: 50, y0: 100, x1: 400, y1: 120, filtered: false },
  { index: 1, text: 'Sentence 1.', page: 0, x0: 50, y0: 130, x1: 400, y1: 150, filtered: false },
  { index: 2, text: 'Sentence 2.', page: 0, x0: 50, y0: 160, x1: 400, y1: 180, filtered: false },
  { index: 3, text: 'Sentence 3.', page: 1, x0: 50, y0: 50, x1: 400, y1: 70, filtered: false },
  { index: 4, text: 'Sentence 4.', page: 1, x0: 50, y0: 80, x1: 400, y1: 100, filtered: false },
  { index: 5, text: 'Sentence 5.', page: 1, x0: 50, y0: 110, x1: 400, y1: 130, filtered: false },
]

// Minimal binary chunks used by WsDriver.sendAudioChunk — the method only
// reads .length, not the actual bytes, so these serve as convenient size tags.
export const MOCK_AUDIO_CHUNK = Buffer.alloc(1024)
export const MOCK_AUDIO_CHUNK_SMALL = Buffer.alloc(128)
export function makeMinimalPdf(): Buffer {
  const lines: string[] = ['%PDF-1.4']
  const offsets: number[] = []

  function addObj(content: string) {
    const n = offsets.length + 1
    offsets.push(Buffer.byteLength(lines.join('\n') + '\n'))
    lines.push(`${n} 0 obj`, content, 'endobj')
  }

  addObj('<</Type /Catalog /Pages 2 0 R>>')
  addObj('<</Type /Pages /Kids [3 0 R 4 0 R] /Count 2>>')
  addObj('<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>')
  addObj('<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>')

  const xrefOffset = Buffer.byteLength(lines.join('\n') + '\n')
  lines.push(
    'xref',
    `0 ${offsets.length + 1}`,
    '0000000000 65535 f ',
    ...offsets.map(o => String(o).padStart(10, '0') + ' 00000 n '),
    'trailer',
    `<</Size ${offsets.length + 1} /Root 1 0 R>>`,
    'startxref',
    String(xrefOffset),
    '%%EOF',
  )
  return Buffer.from(lines.join('\n'))
}
