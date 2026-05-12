import { describe, it, expect } from 'vitest'
import {
  bionifyWord,
  bionifyText,
  bionifyTextToSegments,
  COMMON_WORDS,
  DEFAULT_BIONIC_OPTIONS,
} from '$lib/utils/bionic-reading'

describe('bionifyWord', () => {
  it('bolds first half of "reading"', () => {
    const result = bionifyWord('reading')
    expect(result.bold).toBe('read')
    expect(result.rest).toBe('ing')
    expect(result.original).toBe('reading')
  })

  it('bolds first half of "Bionic"', () => {
    const result = bionifyWord('Bionic')
    expect(result.bold).toBe('Bio')
    expect(result.rest).toBe('nic')
    expect(result.original).toBe('Bionic')
  })

  it('leaves 1-character words untouched', () => {
    const result = bionifyWord('a')
    expect(result.bold).toBe('')
    expect(result.rest).toBe('a')
  })

  it('leaves 2-character words untouched', () => {
    const result = bionifyWord('or')
    expect(result.bold).toBe('')
    expect(result.rest).toBe('or')
  })

  it('bolds first 2 characters of 3-letter word', () => {
    const result = bionifyWord('cat')
    expect(result.bold).toBe('ca')
    expect(result.rest).toBe('t')
  })

  it('bolds first 2 characters of 4-letter word', () => {
    const result = bionifyWord('word')
    expect(result.bold).toBe('wo')
    expect(result.rest).toBe('rd')
  })

  it('skips common English words by default', () => {
    expect(bionifyWord('the').bold).toBe('')
    expect(bionifyWord('and').bold).toBe('')
    expect(bionifyWord('for').bold).toBe('')
    expect(bionifyWord('this').bold).toBe('')
  })

  it('processes common words when skipCommonWords is false', () => {
    const result = bionifyWord('the', { skipCommonWords: false })
    expect(result.bold).toBe('th')
    expect(result.rest).toBe('e')
  })

  it('handles trailing punctuation', () => {
    const result = bionifyWord('reading,')
    expect(result.bold).toBe('read')
    expect(result.rest).toBe('ing,')
  })

  it('handles leading punctuation', () => {
    const result = bionifyWord('"Hello')
    expect(result.bold).toBe('"Hel')
    expect(result.rest).toBe('lo')
  })

  it('handles surrounding punctuation', () => {
    const result = bionifyWord('"world!"')
    expect(result.bold).toBe('"wor')
    expect(result.rest).toBe('ld!"')
  })

  it('handles words with internal apostrophes', () => {
    const result = bionifyWord("don't")
    expect(result.bold).toBe("don")
    expect(result.rest).toBe("'t")
    expect(result.original).toBe("don't")
  })

  it('handles hyphenated words as a single word', () => {
    const result = bionifyWord('well-known')
    expect(result.bold).toBe('well-')
    expect(result.rest).toBe('known')
  })

  it('returns empty strings for empty input', () => {
    const result = bionifyWord('')
    expect(result.bold).toBe('')
    expect(result.rest).toBe('')
    expect(result.original).toBe('')
  })

  it('respects custom boldRatio=0.3', () => {
    const result = bionifyWord('reading', { boldRatio: 0.3 })
    expect(result.bold).toBe('rea')
    expect(result.rest).toBe('ding')
  })

  it('respects custom boldRatio=0.7', () => {
    const result = bionifyWord('reading', { boldRatio: 0.7 })
    expect(result.bold).toBe('readi')
    expect(result.rest).toBe('ng')
  })

  it('respects fixationPoint=1 (maps to ratio 0.50)', () => {
    const result = bionifyWord('reading', { fixationPoint: 1 })
    expect(result.bold).toBe('read')
    expect(result.rest).toBe('ing')
  })

  it('respects fixationPoint=3 (maps to ratio 0.40)', () => {
    const result = bionifyWord('reading', { fixationPoint: 3 })
    expect(result.bold).toBe('rea')
    expect(result.rest).toBe('ding')
  })

  it('respects fixationPoint=5 (maps to ratio 0.30)', () => {
    const result = bionifyWord('reading', { fixationPoint: 5 })
    expect(result.bold).toBe('rea')
    expect(result.rest).toBe('ding')
  })

  it('boldRatio takes precedence over fixationPoint', () => {
    const result = bionifyWord('reading', { fixationPoint: 5, boldRatio: 0.5 })
    expect(result.bold).toBe('read')
    expect(result.rest).toBe('ing')
  })

  it('handles single character with punctuation', () => {
    const result = bionifyWord('"a"')
    expect(result.bold).toBe('')
    expect(result.rest).toBe('"a"')
  })

  it('processes uppercase common words case-insensitively', () => {
    expect(bionifyWord('The').bold).toBe('')
    expect(bionifyWord('AND').bold).toBe('')
  })

  it('handles purely punctuation input as untouched word', () => {
    const result = bionifyWord('...')
    expect(result.bold).toBe('')
    expect(result.rest).toBe('...')
  })

  it('respects minWordLength option', () => {
    expect(bionifyWord('hi', { minWordLength: 2 }).bold).toBe('h')
    expect(bionifyWord('hi', { minWordLength: 3 }).bold).toBe('')
    expect(bionifyWord('cat', { minWordLength: 4 }).bold).toBe('')
  })
})

describe('bionifyText', () => {
  it('returns array of BionicWord for a full sentence', () => {
    const result = bionifyText('Bionic Reading is great')
    expect(result).toHaveLength(4)
    expect(result[0].bold).toBe('Bio')
    expect(result[0].rest).toBe('nic')
    expect(result[0].original).toBe('Bionic')
    expect(result[1].bold).toBe('Read')
    expect(result[1].rest).toBe('ing')
    expect(result[1].original).toBe('Reading')
    expect(result[2].bold).toBe('')
    expect(result[2].rest).toBe('is')
    expect(result[2].original).toBe('is')
    expect(result[3].bold).toBe('gre')
    expect(result[3].rest).toBe('at')
    expect(result[3].original).toBe('great')
  })

  it('returns empty array for empty string', () => {
    expect(bionifyText('')).toEqual([])
  })

  it('returns empty array for whitespace-only string', () => {
    expect(bionifyText('   ')).toEqual([])
    expect(bionifyText('\t\n')).toEqual([])
  })

  it('handles single word', () => {
    const result = bionifyText('hello')
    expect(result).toHaveLength(1)
    expect(result[0].bold).toBe('hel')
    expect(result[0].rest).toBe('lo')
  })

  it('preserves punctuation in sentence', () => {
    const result = bionifyText('Hello, world!')
    expect(result[0].bold).toBe('Hel')
    expect(result[0].rest).toBe('lo,')
    expect(result[1].bold).toBe('wor')
    expect(result[1].rest).toBe('ld!')
  })

  it('strips leading whitespace', () => {
    const result = bionifyText('  hello')
    expect(result).toHaveLength(1)
    expect(result[0].original).toBe('hello')
  })

  it('strips trailing whitespace', () => {
    const result = bionifyText('hello  ')
    expect(result).toHaveLength(1)
  })

  it('handles consecutive whitespace as single separator', () => {
    const result = bionifyText('cat    dog')
    expect(result).toHaveLength(2)
    expect(result[0].original).toBe('cat')
    expect(result[1].original).toBe('dog')
  })

  it('handles words with numbers', () => {
    const result = bionifyText('page 1000')
    expect(result[0].bold).toBe('pa')
    expect(result[0].rest).toBe('ge')
    expect(result[1].original).toBe('1000')
    expect(result[1].bold).toBe('10')
    expect(result[1].rest).toBe('00')
  })
})

describe('bionifyTextToSegments', () => {
  it('returns flat array of segments for rendering', () => {
    const result = bionifyTextToSegments('cat')
    expect(result).toEqual([
      { text: 'ca', bold: true },
      { text: 't', bold: false },
    ])
  })

  it('includes whitespace segments between words', () => {
    const result = bionifyTextToSegments('cat dog')
    expect(result).toEqual([
      { text: 'ca', bold: true },
      { text: 't', bold: false },
      { text: ' ', bold: false },
      { text: 'do', bold: true },
      { text: 'g', bold: false },
    ])
  })

  it('handles untouched words with no whitespace', () => {
    const result = bionifyTextToSegments('a')
    expect(result).toEqual([
      { text: 'a', bold: false },
    ])
  })

  it('handles empty string', () => {
    expect(bionifyTextToSegments('')).toEqual([])
  })

  it('preserves whitespace-only input', () => {
    const result = bionifyTextToSegments('   ')
    expect(result).toEqual([
      { text: '   ', bold: false },
    ])
  })

  it('handles leading whitespace', () => {
    const result = bionifyTextToSegments('  cat')
    expect(result).toEqual([
      { text: '  ', bold: false },
      { text: 'ca', bold: true },
      { text: 't', bold: false },
    ])
  })

  it('handles trailing whitespace', () => {
    const result = bionifyTextToSegments('cat  ')
    expect(result).toEqual([
      { text: 'ca', bold: true },
      { text: 't', bold: false },
      { text: '  ', bold: false },
    ])
  })

  it('skips common words and includes their whitespace', () => {
    const result = bionifyTextToSegments('the cat')
    expect(result).toEqual([
      { text: 'the', bold: false },
      { text: ' ', bold: false },
      { text: 'ca', bold: true },
      { text: 't', bold: false },
    ])
  })

  it('preserves multiple spaces between words', () => {
    const result = bionifyTextToSegments('cat  dog')
    expect(result).toEqual([
      { text: 'ca', bold: true },
      { text: 't', bold: false },
      { text: '  ', bold: false },
      { text: 'do', bold: true },
      { text: 'g', bold: false },
    ])
  })

  it('handles sentence with trailing punctuation', () => {
    const result = bionifyTextToSegments('Hello, world!')
    expect(result).toEqual([
      { text: 'Hel', bold: true },
      { text: 'lo,', bold: false },
      { text: ' ', bold: false },
      { text: 'wor', bold: true },
      { text: 'ld!', bold: false },
    ])
  })

  it('handles sentence without trailing punctuation', () => {
    const result = bionifyTextToSegments('Bionic Reading')
    expect(result).toEqual([
      { text: 'Bio', bold: true },
      { text: 'nic', bold: false },
      { text: ' ', bold: false },
      { text: 'Read', bold: true },
      { text: 'ing', bold: false },
    ])
  })

  it('handles tab-separated words', () => {
    const result = bionifyTextToSegments('cat\tdog')
    expect(result).toEqual([
      { text: 'ca', bold: true },
      { text: 't', bold: false },
      { text: '\t', bold: false },
      { text: 'do', bold: true },
      { text: 'g', bold: false },
    ])
  })

  it('handles newline-separated words', () => {
    const result = bionifyTextToSegments('hello\nworld')
    expect(result).toEqual([
      { text: 'hel', bold: true },
      { text: 'lo', bold: false },
      { text: '\n', bold: false },
      { text: 'wor', bold: true },
      { text: 'ld', bold: false },
    ])
  })
})

describe('COMMON_WORDS', () => {
  it('contains common English words', () => {
    expect(COMMON_WORDS.has('the')).toBe(true)
    expect(COMMON_WORDS.has('and')).toBe(true)
    expect(COMMON_WORDS.has('for')).toBe(true)
    expect(COMMON_WORDS.has('this')).toBe(true)
    expect(COMMON_WORDS.has('that')).toBe(true)
    expect(COMMON_WORDS.has('with')).toBe(true)
    expect(COMMON_WORDS.has('have')).toBe(true)
    expect(COMMON_WORDS.has('not')).toBe(true)
  })

  it('does not contain unusual words', () => {
    expect(COMMON_WORDS.has('cat')).toBe(false)
    expect(COMMON_WORDS.has('reading')).toBe(false)
    expect(COMMON_WORDS.has('Bionic')).toBe(false)
    expect(COMMON_WORDS.has('hello')).toBe(false)
  })
})

describe('DEFAULT_BIONIC_OPTIONS', () => {
  it('has expected default values', () => {
    expect(DEFAULT_BIONIC_OPTIONS.fixationPoint).toBe(1)
    expect(DEFAULT_BIONIC_OPTIONS.minWordLength).toBe(3)
    expect(DEFAULT_BIONIC_OPTIONS.skipCommonWords).toBe(true)
  })
})

describe('performance', () => {
  it('processes 9000 words (1000 sentences) in less than 50ms', () => {
    const sentences: string[] = []
    for (let i = 0; i < 1000; i++) {
      sentences.push('Bionic Reading is a new way to read text')
    }
    const text = sentences.join(' ')
    const start = performance.now()
    bionifyTextToSegments(text)
    const elapsed = performance.now() - start
    expect(elapsed).toBeLessThan(50)
  })
})
