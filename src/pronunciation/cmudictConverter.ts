import type {
  PronunciationEntry,
  StressLevel,
  VowelPhoneme,
  WordPronunciation,
} from './pronunciationTypes'

const ARPABET_TO_IPA: Record<string, string> = {
  AA: 'ɑ',
  AE: 'æ',
  AH: 'ʌ',
  AO: 'ɔ',
  AW: 'aʊ',
  AY: 'aɪ',
  EH: 'ɛ',
  ER: 'ɝ',
  EY: 'eɪ',
  IH: 'ɪ',
  IY: 'iː',
  OW: 'oʊ',
  OY: 'ɔɪ',
  UH: 'ʊ',
  UW: 'uː',

  B: 'b',
  CH: 'tʃ',
  D: 'd',
  DH: 'ð',
  F: 'f',
  G: 'ɡ',
  HH: 'h',
  JH: 'dʒ',
  K: 'k',
  L: 'l',
  M: 'm',
  N: 'n',
  NG: 'ŋ',
  P: 'p',
  R: 'r',
  S: 's',
  SH: 'ʃ',
  T: 't',
  TH: 'θ',
  V: 'v',
  W: 'w',
  Y: 'j',
  Z: 'z',
  ZH: 'ʒ',
}

const VOWELS = new Set([
  'AA',
  'AE',
  'AH',
  'AO',
  'AW',
  'AY',
  'EH',
  'ER',
  'EY',
  'IH',
  'IY',
  'OW',
  'OY',
  'UH',
  'UW',
])

type ParsedPhoneme = {
  arpabet: string
  base: string
  stress: StressLevel | null
}

function parsePhoneme(phoneme: string): ParsedPhoneme {
  const match = phoneme.match(/^([A-Z]+)([012])?$/)

  if (!match) {
    return {
      arpabet: phoneme,
      base: phoneme,
      stress: null,
    }
  }

  return {
    arpabet: phoneme,
    base: match[1],
    stress:
      match[2] === undefined
        ? null
        : (Number(match[2]) as StressLevel),
  }
}

function phonemeToIpa(phoneme: ParsedPhoneme): string {
  if (phoneme.base === 'AH' && phoneme.stress === 0) {
    return 'ə'
  }

  if (phoneme.base === 'ER' && phoneme.stress === 0) {
    return 'ɚ'
  }

  return ARPABET_TO_IPA[phoneme.base] ?? ''
}

function getVowels(phonemes: string[]): VowelPhoneme[] {
  return phonemes
    .map(parsePhoneme)
    .filter(
      (
        phoneme,
      ): phoneme is ParsedPhoneme & { stress: StressLevel } =>
        VOWELS.has(phoneme.base) && phoneme.stress !== null,
    )
    .map((phoneme) => ({
      arpabet: phoneme.arpabet,
      ipa: phonemeToIpa(phoneme),
      stress: phoneme.stress,
    }))
}

function getPrimaryVowel(vowels: VowelPhoneme[]): string | null {
  const primaryStress = vowels.find((vowel) => vowel.stress === 1)

  if (primaryStress) {
    return primaryStress.ipa
  }

  const secondaryStress = vowels.find((vowel) => vowel.stress === 2)

  if (secondaryStress) {
    return secondaryStress.ipa
  }

  return vowels[0]?.ipa ?? null
}

function createLearnerIpa(phonemes: string[]): string {
  return '/' + phonemes.map((phoneme) =>
    phonemeToIpa(parsePhoneme(phoneme)),
  ).join('') + '/'
}

export function convertCmudictPronunciation(
  phonemes: string[],
): PronunciationEntry {
  const vowels = getVowels(phonemes)

  return {
    ipa: createLearnerIpa(phonemes),
    phonemes,
    vowels,
    primaryVowel: getPrimaryVowel(vowels),
  }
}

export function createWordPronunciation(
  word: string,
  variants: string[][],
): WordPronunciation {
  return {
    word,
    pronunciations: variants.map(convertCmudictPronunciation),
    meanings: {},
    source: 'cmudict',
  }
}
