export interface BionicWord {
  bold: string
  rest: string
  original: string
}

export interface BionicSegment {
  text: string
  bold: boolean
}

export interface BionicOptions {
  fixationPoint: number
  boldRatio?: number
  minWordLength: number
  skipCommonWords: boolean
}

export const DEFAULT_BIONIC_OPTIONS: BionicOptions = {
  fixationPoint: 1,
  minWordLength: 3,
  skipCommonWords: true,
}

export const COMMON_WORDS = new Set([
  'a', 'an', 'the',
  'and', 'or', 'but', 'nor', 'yet', 'so',
  'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with', 'from',
  'is', 'it', 'be', 'as',
  'are', 'was', 'were', 'been', 'have', 'has', 'had',
  'do', 'does', 'did',
  'not', 'no',
  'if', 'all',
  'its', 'can', 'may', 'will',
  'this', 'that', 'these', 'those',
])

const LEADING_PUNCTUATION = /^["'«¿¡({\[\]]+/
const TRAILING_PUNCTUATION = /[.,!?;:…"'»)\]}\-\u2014\u2013]+$/

function getEffectiveBoldRatio(options: BionicOptions): number {
  return options.boldRatio ?? (0.5 - (options.fixationPoint - 1) * 0.05)
}

function splitPunctuation(word: string): { prefix: string; core: string; suffix: string } {
  const prefixMatch = word.match(LEADING_PUNCTUATION)
  const prefix = prefixMatch ? prefixMatch[0] : ''
  const afterPrefix = prefixMatch ? word.slice(prefix.length) : word
  const suffixMatch = afterPrefix.match(TRAILING_PUNCTUATION)
  const core = suffixMatch ? afterPrefix.slice(0, -suffixMatch[0].length) : afterPrefix
  const suffix = suffixMatch ? afterPrefix.slice(core.length) : ''
  return { prefix, core, suffix }
}

function isCommonWord(word: string): boolean {
  return COMMON_WORDS.has(word.toLowerCase())
}

function mergeOptions(partial?: Partial<BionicOptions>): BionicOptions {
  return { ...DEFAULT_BIONIC_OPTIONS, ...partial }
}

function processWord(word: string, opts: BionicOptions): BionicWord {
  if (word.length === 0) return { bold: '', rest: '', original: '' }
  const { prefix, core, suffix } = splitPunctuation(word)
  if (core.length < opts.minWordLength) {
    return { bold: '', rest: word, original: word }
  }
  if (opts.skipCommonWords && isCommonWord(core)) {
    return { bold: '', rest: word, original: word }
  }
  const boldRatio = getEffectiveBoldRatio(opts)
  const boldCount = Math.ceil(core.length * boldRatio)
  const boldPart = core.slice(0, boldCount)
  const restPart = core.slice(boldCount)
  return {
    bold: prefix + boldPart,
    rest: restPart + suffix,
    original: word,
  }
}

export function bionifyWord(word: string, options?: Partial<BionicOptions>): BionicWord {
  return processWord(word, mergeOptions(options))
}

export function bionifyText(text: string, options?: Partial<BionicOptions>): BionicWord[] {
  if (text.length === 0) return []
  const opts = mergeOptions(options)
  return text.split(/\s+/).filter(w => w.length > 0).map(w => processWord(w, opts))
}

export function bionifyTextToSegments(text: string, options?: Partial<BionicOptions>): BionicSegment[] {
  if (text.length === 0) return []
  const opts = mergeOptions(options)
  const parts = text.split(/(\s+)/)
  const segments: BionicSegment[] = []
  for (const part of parts) {
    if (part.length === 0) continue
    if (/^\s+$/.test(part)) {
      segments.push({ text: part, bold: false })
    } else {
      const bw = processWord(part, opts)
      if (bw.bold) segments.push({ text: bw.bold, bold: true })
      if (bw.rest) segments.push({ text: bw.rest, bold: false })
    }
  }
  return segments
}
