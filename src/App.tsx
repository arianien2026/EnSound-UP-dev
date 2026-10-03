import { useEffect, useMemo, useRef, useState } from 'react'
import posthog from 'posthog-js'
import { lookupCmudictWord } from './pronunciation/cmudictLookup'
import type { WordPronunciation } from './pronunciation/pronunciationTypes'

type WordToken = {
  raw: string
  spoken: string
  index: number
}

const DEFAULT_SENTENCE = 'I live in the countryside. I leave the countryside.'

function tokenizeSentence(sentence: string): WordToken[] {
  return (sentence.match(/[A-Za-z]+(?:['-][A-Za-z]+)*/g) ?? [])
    .map((raw, index) => ({
      raw,
      spoken: raw,
      index,
    }))
}

function speakWord(word: string, rate: number) {
  if (!('speechSynthesis' in window)) return

  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(word)
  utterance.lang = 'en-US'
  utterance.rate = rate
  utterance.pitch = 1
  window.speechSynthesis.speak(utterance)
}

type SoundMapEntry = { vowel: string; word: string; ipa: string; note: string }

const SOUND_MAP = {
  A: [
    { vowel: 'æ', word: 'cat', ipa: '/kæt/', note: 'cat · map · apple' },
    { vowel: 'eɪ', word: 'name', ipa: '/neɪm/', note: 'name · cake · late' },
    { vowel: 'ɑ', word: 'father', ipa: '/ˈfɑðɚ/', note: 'father' },
    { vowel: 'ɔ', word: 'all', ipa: '/ɔl/', note: 'all · ball' },
    { vowel: 'ə', word: 'about', ipa: '/əˈbaʊt/', note: 'about · ago · away' },
    { vowel: 'ɛ', word: 'any', ipa: '/ˈɛni/', note: 'any · many' },
    { vowel: 'ɪ', word: 'village', ipa: '/ˈvɪlɪdʒ/', note: 'village' },
  ],
  E: [
    { vowel: 'ɛ', word: 'bed', ipa: '/bɛd/', note: 'bed' },
    { vowel: 'iː', word: 'me', ipa: '/miː/', note: 'me' },
    { vowel: 'ɪ', word: 'pretty', ipa: '/ˈprɪti/', note: 'pretty' },
    { vowel: 'ə', word: 'problem', ipa: '/ˈprɑbləm/', note: 'problem' },
  ],
  I: [
    { vowel: 'ɪ', word: 'sit', ipa: '/sɪt/', note: 'sit' },
    { vowel: 'aɪ', word: 'time', ipa: '/taɪm/', note: 'time' },
    { vowel: 'iː', word: 'machine', ipa: '/məˈʃiːn/', note: 'machine' },
    { vowel: 'ə', word: 'pencil', ipa: '/ˈpɛnsəl/', note: 'pencil' },
  ],
  O: [
    { vowel: 'ɑ', word: 'hot', ipa: '/hɑt/', note: 'hot' },
    { vowel: 'oʊ', word: 'home', ipa: '/hoʊm/', note: 'home' },
    { vowel: 'ʌ', word: 'love', ipa: '/lʌv/', note: 'love' },
    { vowel: 'uː', word: 'do', ipa: '/duː/', note: 'do' },
    { vowel: 'ʊ', word: 'woman', ipa: '/ˈwʊmən/', note: 'woman' },
    { vowel: 'ə', word: 'today', ipa: '/təˈdeɪ/', note: 'today' },
  ],
  U: [
    { vowel: 'ʌ', word: 'cup', ipa: '/kʌp/', note: 'cup' },
    { vowel: 'juː', word: 'use', ipa: '/juːz/', note: 'use' },
    { vowel: 'uː', word: 'rule', ipa: '/ruːl/', note: 'rule' },
    { vowel: 'ʊ', word: 'put', ipa: '/pʊt/', note: 'put' },
    { vowel: 'ə', word: 'support', ipa: '/səˈpɔrt/', note: 'support' },
  ],
} satisfies Record<'A' | 'E' | 'I' | 'O' | 'U', SoundMapEntry[]>

const VOWEL_WORD_SUPPORT: Record<string, { partOfSpeech: string; meaningZh: string }> = {
  about: { partOfSpeech: 'prep.', meaningZh: '關於' },
  adept: { partOfSpeech: 'adj.', meaningZh: '熟練的' },
  adopt: { partOfSpeech: 'v.', meaningZh: '收養' },
  ago: { partOfSpeech: 'adv.', meaningZh: '以前' },
  all: { partOfSpeech: 'pron.', meaningZh: '全部' },
  any: { partOfSpeech: 'det.', meaningZh: '任何' },
  apple: { partOfSpeech: 'n.', meaningZh: '蘋果' },
  audible: { partOfSpeech: 'adj.', meaningZh: '聽得見的' },
  away: { partOfSpeech: 'adv.', meaningZh: '在遠處' },
  bad: { partOfSpeech: 'adj.', meaningZh: '壞的' },
  ball: { partOfSpeech: 'n.', meaningZh: '球' },
  bat: { partOfSpeech: 'n.', meaningZh: '蝙蝠' },
  bead: { partOfSpeech: 'n.', meaningZh: '珠子' },
  bean: { partOfSpeech: 'n.', meaningZh: '豆子' },
  beat: { partOfSpeech: 'v.', meaningZh: '敲打' },
  bed: { partOfSpeech: 'n.', meaningZh: '床' },
  bet: { partOfSpeech: 'v.', meaningZh: '打賭' },
  bid: { partOfSpeech: 'v.', meaningZh: '出價' },
  big: { partOfSpeech: 'adj.', meaningZh: '大的' },
  bit: { partOfSpeech: 'n.', meaningZh: '一小塊' },
  bog: { partOfSpeech: 'n.', meaningZh: '沼澤' },
  bomb: { partOfSpeech: 'n.', meaningZh: '炸彈' },
  boom: { partOfSpeech: 'n.', meaningZh: '巨響' },
  boon: { partOfSpeech: 'n.', meaningZh: '好處' },
  boot: { partOfSpeech: 'n.', meaningZh: '靴子' },
  bot: { partOfSpeech: 'n.', meaningZh: '機器人' },
  bud: { partOfSpeech: 'n.', meaningZh: '花苞' },
  bun: { partOfSpeech: 'n.', meaningZh: '小圓麵包' },
  but: { partOfSpeech: 'conj.', meaningZh: '但是' },
  cake: { partOfSpeech: 'n.', meaningZh: '蛋糕' },
  cap: { partOfSpeech: 'n.', meaningZh: '帽子' },
  cat: { partOfSpeech: 'n.', meaningZh: '貓' },
  coop: { partOfSpeech: 'n.', meaningZh: '雞舍' },
  cop: { partOfSpeech: 'n.', meaningZh: '警察' },
  cot: { partOfSpeech: 'n.', meaningZh: '折疊床' },
  cup: { partOfSpeech: 'n.', meaningZh: '杯子' },
  cut: { partOfSpeech: 'v.', meaningZh: '切' },
  did: { partOfSpeech: 'v.', meaningZh: '做了' },
  do: { partOfSpeech: 'v.', meaningZh: '做' },
  dude: { partOfSpeech: 'n.', meaningZh: '老兄' },
  eater: { partOfSpeech: 'n.', meaningZh: '吃東西的人' },
  edible: { partOfSpeech: 'adj.', meaningZh: '可食用的' },
  fat: { partOfSpeech: 'adj.', meaningZh: '胖的' },
  father: { partOfSpeech: 'n.', meaningZh: '父親' },
  feel: { partOfSpeech: 'v.', meaningZh: '感覺' },
  feet: { partOfSpeech: 'n.', meaningZh: '腳（複數）' },
  fell: { partOfSpeech: 'v.', meaningZh: '跌倒了' },
  fig: { partOfSpeech: 'n.', meaningZh: '無花果' },
  fit: { partOfSpeech: 'adj.', meaningZh: '健康的' },
  fog: { partOfSpeech: 'n.', meaningZh: '霧' },
  fool: { partOfSpeech: 'n.', meaningZh: '傻瓜' },
  foot: { partOfSpeech: 'n.', meaningZh: '腳' },
  full: { partOfSpeech: 'adj.', meaningZh: '滿的' },
  god: { partOfSpeech: 'n.', meaningZh: '神' },
  good: { partOfSpeech: 'adj.', meaningZh: '好的' },
  head: { partOfSpeech: 'n.', meaningZh: '頭' },
  heat: { partOfSpeech: 'n.', meaningZh: '熱' },
  home: { partOfSpeech: 'n.', meaningZh: '家' },
  hot: { partOfSpeech: 'adj.', meaningZh: '熱的' },
  hut: { partOfSpeech: 'n.', meaningZh: '小屋' },
  late: { partOfSpeech: 'adj.', meaningZh: '晚的' },
  less: { partOfSpeech: 'det.', meaningZh: '較少的' },
  look: { partOfSpeech: 'v.', meaningZh: '看' },
  loose: { partOfSpeech: 'adj.', meaningZh: '鬆的' },
  love: { partOfSpeech: 'n.', meaningZh: '愛' },
  luck: { partOfSpeech: 'n.', meaningZh: '運氣' },
  machine: { partOfSpeech: 'n.', meaningZh: '機器' },
  many: { partOfSpeech: 'det.', meaningZh: '許多' },
  map: { partOfSpeech: 'n.', meaningZh: '地圖' },
  me: { partOfSpeech: 'pron.', meaningZh: '我' },
  name: { partOfSpeech: 'n.', meaningZh: '名字' },
  otter: { partOfSpeech: 'n.', meaningZh: '水獺' },
  pat: { partOfSpeech: 'v.', meaningZh: '輕拍' },
  pencil: { partOfSpeech: 'n.', meaningZh: '鉛筆' },
  pet: { partOfSpeech: 'n.', meaningZh: '寵物' },
  pit: { partOfSpeech: 'n.', meaningZh: '坑' },
  pool: { partOfSpeech: 'n.', meaningZh: '游泳池' },
  pot: { partOfSpeech: 'n.', meaningZh: '鍋子' },
  pretty: { partOfSpeech: 'adj.', meaningZh: '漂亮的' },
  problem: { partOfSpeech: 'n.', meaningZh: '問題' },
  pull: { partOfSpeech: 'v.', meaningZh: '拉' },
  put: { partOfSpeech: 'v.', meaningZh: '放' },
  rule: { partOfSpeech: 'n.', meaningZh: '規則' },
  sack: { partOfSpeech: 'n.', meaningZh: '麻布袋' },
  sheep: { partOfSpeech: 'n.', meaningZh: '羊' },
  ship: { partOfSpeech: 'n.', meaningZh: '船' },
  sit: { partOfSpeech: 'v.', meaningZh: '坐' },
  sock: { partOfSpeech: 'n.', meaningZh: '襪子' },
  support: { partOfSpeech: 'v.', meaningZh: '支持' },
  time: { partOfSpeech: 'n.', meaningZh: '時間' },
  today: { partOfSpeech: 'adv.', meaningZh: '今天' },
  took: { partOfSpeech: 'v.', meaningZh: '拿了' },
  tuck: { partOfSpeech: 'v.', meaningZh: '塞入' },
  use: { partOfSpeech: 'v.', meaningZh: '使用' },
  village: { partOfSpeech: 'n.', meaningZh: '村莊' },
  woman: { partOfSpeech: 'n.', meaningZh: '女人' },
}

type SoundMapLetter = keyof typeof SOUND_MAP
const SOUND_MAP_LETTERS = Object.keys(SOUND_MAP) as SoundMapLetter[]

type ConsonantSound = { sound: string; word: string; ipa: string; partOfSpeech: string; meaningZh: string }
type ConsonantPair = {
  id: string
  sounds: readonly [ConsonantSound, ConsonantSound]
  noteZh: string
  noteEn: string
}

type TestBankItem = { vowel: string; word: string; ipa: string; frame: string; contrast?: readonly string[]; diagnosticContrast?: string }
type ConsonantTestQuestion = { pairIndex: number; targetIndex: 0 | 1; choices: string[] }

function shuffled<T>(items: readonly T[]): T[] {
  const result = [...items]
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[result[i], result[j]] = [result[j], result[i]]
  }
  return result
}

function vowelTestChoices(target: TestBankItem, bank: readonly TestBankItem[]): TestBankItem[] {
  const choices = [target]
  const add = (vowel: string) => {
    if (choices.some((item) => item.vowel === vowel)) return
    const item = bank.find((candidate) => candidate.vowel === vowel && candidate.frame === target.frame)
      ?? bank.find((candidate) => candidate.vowel === vowel)
    if (item) choices.push(item)
  }
  target.contrast?.forEach(add)
  shuffled([...new Set(bank.map((item) => item.vowel))]).forEach((vowel) => {
    if (choices.length < 4) add(vowel)
  })
  return shuffled(choices.slice(0, 4))
}

function weakContrastRecommendations(mistakes: Record<string, {count: number; chosen: Record<string, number>}>) {
  const merged = new Map<string, {target: string; chosen: string; count: number}>()
  for (const [target, detail] of Object.entries(mistakes)) {
    for (const [chosen, count] of Object.entries(detail.chosen)) {
      const key = [target, chosen].sort().join('|')
      const existing = merged.get(key)
      if (existing) existing.count += count
      else merged.set(key, {target, chosen, count})
    }
  }
  return [...merged.values()].sort((a, b) => b.count - a.count)
}

function consonantTestRound(): ConsonantTestQuestion[] {
  const inventory = [...new Set(CONSONANT_PAIRS.flatMap((pair) => pair.sounds.map((item) => item.sound)))]
  return shuffled(CONSONANT_PAIRS.map((_, pairIndex) => pairIndex)).slice(0, 10).map((pairIndex) => {
    const pair = CONSONANT_PAIRS[pairIndex]
    const targetIndex: 0 | 1 = Math.random() < 0.5 ? 0 : 1
    const target = pair.sounds[targetIndex].sound
    const partner = pair.sounds[1 - targetIndex].sound
    const related = CONSONANT_PAIRS.filter((candidate) =>
      candidate.sounds.some((item) => item.sound === target || item.sound === partner))
      .flatMap((candidate) => candidate.sounds.map((item) => item.sound))
    const choices = [target, partner]
    for (const sound of [...shuffled([...new Set(related)]), ...shuffled(inventory)]) {
      if (!choices.includes(sound)) choices.push(sound)
      if (choices.length === 4) break
    }
    return { pairIndex, targetIndex, choices: shuffled(choices) }
  })
}

const CONSONANT_PAIRS: readonly ConsonantPair[] = [
  { id: 'p-b', sounds: [{ sound: 'p', word: 'pat', ipa: '/pæt/', partOfSpeech: 'v.', meaningZh: '輕拍' }, { sound: 'b', word: 'bat', ipa: '/bæt/', partOfSpeech: 'n.', meaningZh: '蝙蝠' }], noteZh: '只有開頭子音不同；/æ/ 和 /t/ 相同。', noteEn: 'Only the first consonant changes; /æ/ and /t/ stay the same.' },
  { id: 't-d', sounds: [{ sound: 't', word: 'ten', ipa: '/tɛn/', partOfSpeech: 'num.', meaningZh: '十' }, { sound: 'd', word: 'den', ipa: '/dɛn/', partOfSpeech: 'n.', meaningZh: '獸穴' }], noteZh: '只有開頭子音不同；/ɛn/ 相同。', noteEn: 'Only the first consonant changes; /ɛn/ stays the same.' },
  { id: 'k-g', sounds: [{ sound: 'k', word: 'coat', ipa: '/koʊt/', partOfSpeech: 'n.', meaningZh: '外套' }, { sound: 'g', word: 'goat', ipa: '/goʊt/', partOfSpeech: 'n.', meaningZh: '山羊' }], noteZh: '只有開頭子音不同；/oʊt/ 相同。', noteEn: 'Only the first consonant changes; /oʊt/ stays the same.' },
  { id: 'f-v', sounds: [{ sound: 'f', word: 'fan', ipa: '/fæn/', partOfSpeech: 'n.', meaningZh: '電風扇' }, { sound: 'v', word: 'van', ipa: '/væn/', partOfSpeech: 'n.', meaningZh: '廂型車' }], noteZh: '只有開頭子音不同；/æn/ 相同。', noteEn: 'Only the first consonant changes; /æn/ stays the same.' },
  { id: 'θ-ð', sounds: [{ sound: 'θ', word: 'teeth', ipa: '/tiːθ/', partOfSpeech: 'n.', meaningZh: '牙齒' }, { sound: 'ð', word: 'teethe', ipa: '/tiːð/', partOfSpeech: 'v.', meaningZh: '長牙' }], noteZh: '只有最後的子音不同；/tiː/ 相同。', noteEn: 'Only the final consonant changes; /tiː/ stays the same.' },
  { id: 's-z', sounds: [{ sound: 's', word: 'sip', ipa: '/sɪp/', partOfSpeech: 'v.', meaningZh: '小口喝' }, { sound: 'z', word: 'zip', ipa: '/zɪp/', partOfSpeech: 'v.', meaningZh: '拉拉鍊' }], noteZh: '只有開頭子音不同；/ɪp/ 相同。', noteEn: 'Only the first consonant changes; /ɪp/ stays the same.' },
  { id: 'ʃ-ʒ', sounds: [{ sound: 'ʃ', word: 'pressure', ipa: '/ˈprɛʃɚ/', partOfSpeech: 'n.', meaningZh: '壓力' }, { sound: 'ʒ', word: 'pleasure', ipa: '/ˈplɛʒɚ/', partOfSpeech: 'n.', meaningZh: '愉悅' }], noteZh: '聽字中的 /ʃ/ 與 /ʒ/；開頭的子音也不同。', noteEn: 'Listen for /ʃ/ and /ʒ/ inside the words; their opening consonants differ too.' },
  { id: 'tʃ-dʒ', sounds: [{ sound: 'tʃ', word: 'cheap', ipa: '/tʃiːp/', partOfSpeech: 'adj.', meaningZh: '便宜的' }, { sound: 'dʒ', word: 'jeep', ipa: '/dʒiːp/', partOfSpeech: 'n.', meaningZh: '吉普車' }], noteZh: '只有開頭子音不同；/iːp/ 相同。', noteEn: 'Only the first consonant changes; /iːp/ stays the same.' },
  { id: 'm-n', sounds: [{ sound: 'm', word: 'sum', ipa: '/sʌm/', partOfSpeech: 'n.', meaningZh: '總和' }, { sound: 'n', word: 'sun', ipa: '/sʌn/', partOfSpeech: 'n.', meaningZh: '太陽' }], noteZh: '只有最後的子音不同；/sʌ/ 相同。', noteEn: 'Only the final consonant changes; /sʌ/ stays the same.' },
  { id: 'n-ŋ', sounds: [{ sound: 'n', word: 'sin', ipa: '/sɪn/', partOfSpeech: 'n.', meaningZh: '罪' }, { sound: 'ŋ', word: 'sing', ipa: '/sɪŋ/', partOfSpeech: 'v.', meaningZh: '唱歌' }], noteZh: '只有最後的子音不同；/sɪ/ 相同。', noteEn: 'Only the final consonant changes; /sɪ/ stays the same.' },
  { id: 'l-r', sounds: [{ sound: 'l', word: 'light', ipa: '/laɪt/', partOfSpeech: 'n.', meaningZh: '光' }, { sound: 'r', word: 'right', ipa: '/raɪt/', partOfSpeech: 'adj.', meaningZh: '正確的' }], noteZh: '只有開頭子音不同；/aɪt/ 相同。', noteEn: 'Only the first consonant changes; /aɪt/ stays the same.' },
  { id: 'v-w', sounds: [{ sound: 'v', word: 'vest', ipa: '/vɛst/', partOfSpeech: 'n.', meaningZh: '背心' }, { sound: 'w', word: 'west', ipa: '/wɛst/', partOfSpeech: 'n.', meaningZh: '西方' }], noteZh: '只有開頭子音不同；/ɛst/ 相同。', noteEn: 'Only the first consonant changes; /ɛst/ stays the same.' },
  { id: 's-ʃ', sounds: [{ sound: 's', word: 'see', ipa: '/siː/', partOfSpeech: 'v.', meaningZh: '看見' }, { sound: 'ʃ', word: 'she', ipa: '/ʃiː/', partOfSpeech: 'pron.', meaningZh: '她' }], noteZh: '只有開頭子音不同；/iː/ 相同。', noteEn: 'Only the first consonant changes; /iː/ stays the same.' },
  { id: 'ʃ-tʃ', sounds: [{ sound: 'ʃ', word: 'shop', ipa: '/ʃɑp/', partOfSpeech: 'n.', meaningZh: '商店' }, { sound: 'tʃ', word: 'chop', ipa: '/tʃɑp/', partOfSpeech: 'v.', meaningZh: '劈砍' }], noteZh: '只有開頭子音不同；/ɑp/ 相同。', noteEn: 'Only the first consonant changes; /ɑp/ stays the same.' },
  { id: 'θ-t', sounds: [{ sound: 'θ', word: 'thin', ipa: '/θɪn/', partOfSpeech: 'adj.', meaningZh: '薄的' }, { sound: 't', word: 'tin', ipa: '/tɪn/', partOfSpeech: 'n.', meaningZh: '錫' }], noteZh: '只有開頭子音不同；/ɪn/ 相同。', noteEn: 'Only the first consonant changes; /ɪn/ stays the same.' },
  { id: 'θ-s', sounds: [{ sound: 'θ', word: 'thin', ipa: '/θɪn/', partOfSpeech: 'adj.', meaningZh: '薄的' }, { sound: 's', word: 'sin', ipa: '/sɪn/', partOfSpeech: 'n.', meaningZh: '罪' }], noteZh: '只有開頭子音不同；/ɪn/ 相同。', noteEn: 'Only the first consonant changes; /ɪn/ stays the same.' },
  { id: 'p-f', sounds: [{ sound: 'p', word: 'pat', ipa: '/pæt/', partOfSpeech: 'v.', meaningZh: '輕拍' }, { sound: 'f', word: 'fat', ipa: '/fæt/', partOfSpeech: 'adj.', meaningZh: '胖的' }], noteZh: '只有開頭子音不同；/æt/ 相同。', noteEn: 'Only the first consonant changes; /æt/ stays the same.' },
  { id: 'r-w', sounds: [{ sound: 'r', word: 'right', ipa: '/raɪt/', partOfSpeech: 'adj.', meaningZh: '正確的' }, { sound: 'w', word: 'white', ipa: '/waɪt/', partOfSpeech: 'adj.', meaningZh: '白色的' }], noteZh: '只有開頭子音不同；/aɪt/ 相同。', noteEn: 'Only the first consonant changes; /aɪt/ stays the same.' },
]
const REPEATED_IPA_COLORS: Partial<Record<string, string>> = {
  'ɛ': '#B02BC5',
  'ɪ': '#1D5EFF',
  'ə': '#0097A7',
  'iː': '#009B62',
  'ɑ': '#E45B16',
  'ʌ': '#E13D67',
  'uː': '#008ACB',
  'ʊ': '#779900',
}


const LEVEL1_STAGES = [
  {
    id: '1A',
    title: 'BAT vs BET',
    subtitle: 'Same consonant frame /b_t/. Only the vowel changes.',
    pairs: [
      { left: { vowel: 'æ', word: 'bat', ipa: '/bæt/' }, right: { vowel: 'ɛ', word: 'bet', ipa: '/bɛt/' } },
    ],
  },
  {
    id: '1B',
    title: 'BAD vs BED',
    subtitle: 'Same consonant frame /b_d/. Listen for the middle vowel.',
    pairs: [
      { left: { vowel: 'æ', word: 'bad', ipa: '/bæd/' }, right: { vowel: 'ɛ', word: 'bed', ipa: '/bɛd/' } },
    ],
  },
  {
    id: '1C',
    title: 'Mixed Frames',
    subtitle: 'Now identify /æ/ and /ɛ/ across both consonant frames.',
    pairs: [
      { left: { vowel: 'æ', word: 'bat', ipa: '/bæt/' }, right: { vowel: 'ɛ', word: 'bet', ipa: '/bɛt/' } },
      { left: { vowel: 'æ', word: 'bad', ipa: '/bæd/' }, right: { vowel: 'ɛ', word: 'bed', ipa: '/bɛd/' } },
    ],
  },
]


type FreeUsage = {
  repeat: number
  questions: number
}

const FREE_USAGE_KEY = 'ensound-free-usage'

function DevAccessSwitch({
  accessLevel,
  setAccessLevel,
}: {
  accessLevel: 'free' | 'full'
  setAccessLevel: (level: 'free' | 'full') => void
}) {
  return (
    <div
style={{
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  width: 'fit-content',
  marginBottom: 8,
  padding: '6px 9px',
        borderRadius: 14,
        background: 'white',
        border: '1px solid #dbe5ff',
        boxShadow: '0 4px 18px rgba(36, 64, 120, 0.12)',
        fontSize: 12,
      }}
    >
      <span>🧪 Dev Access</span>

      <button
        type="button"
        onClick={() => setAccessLevel('free')}
        style={{
          border: 0,
          borderRadius: 999,
          padding: '5px 9px',
          cursor: 'pointer',
          background: accessLevel === 'free' ? '#5B8CFF' : '#eef2ff',
          color: accessLevel === 'free' ? 'white' : '#36517f',
        }}
      >
        FREE
      </button>

      <button
        type="button"
        onClick={() => setAccessLevel('full')}
        style={{
          border: 0,
          borderRadius: 999,
          padding: '5px 9px',
          cursor: 'pointer',
          background: accessLevel === 'full' ? '#5B8CFF' : '#eef2ff',
          color: accessLevel === 'full' ? 'white' : '#36517f',
        }}
      >
        FULL
      </button>
    </div>
  )
}

export default function App() {
const [accessLevel, setAccessLevel] = useState<'free' | 'full'>('free')
const isFull = accessLevel === 'full'
type PaywallReason = 'repeat' | 'questions' | null

const [paywallReason, setPaywallReason] = useState<PaywallReason>(null)
  const [showProductInfo, setShowProductInfo] = useState(false)
  const [legalPage, setLegalPage] = useState<'terms' | 'refund' | 'privacy' | null>(null)

  const [freeUsage, setFreeUsage] = useState<FreeUsage>(() => {
    const saved = window.localStorage.getItem(FREE_USAGE_KEY)

    if (saved) {
      try {
        return JSON.parse(saved) as FreeUsage
      } catch {
        // Ignore invalid saved data and start fresh.
      }
    }

    return {
      repeat: 0,
      questions: 0,
    }
  })

  useEffect(() => {
    window.localStorage.setItem(
      FREE_USAGE_KEY,
      JSON.stringify(freeUsage),
    )
  }, [freeUsage])

  const appVisitCapturedRef = useRef(false)

  useEffect(() => {
    if (appVisitCapturedRef.current) return

    posthog.capture('app_visit', {
      app: 'EnSound UP',
      source: 'web',
    })

    appVisitCapturedRef.current = true
  }, [])

  const [uiLang, setUiLang] = useState<'zh-TW' | 'en'>(() => {
    const saved = window.localStorage.getItem('ensound-ui-lang')
    if (saved === 'zh-TW' || saved === 'en') return saved
    return navigator.language.toLowerCase().startsWith('zh') ? 'zh-TW' : 'en'
  })
  const [showVowelBasicsTranslations, setShowVowelBasicsTranslations] = useState(false)
  const isZh = uiLang === 'zh-TW'
  const t = (zh: string, en: string) => (isZh ? zh : en)

  function VowelWordMeaning({ word, inline = false }: { word: string; inline?: boolean }) {
    if (!isZh) return null
    const support = VOWEL_WORD_SUPPORT[word.toLowerCase()]
    if (!support) return null
    return <span style={{ display: inline ? 'inline' : 'block', color: '#7d86aa', fontSize: 14, fontWeight: 400, lineHeight: 1.4 }}>{support.partOfSpeech} {support.meaningZh}</span>
  }

  const isInAppBrowser = useMemo(() => {
    const ua = navigator.userAgent || ''
    return /FBAN|FBAV|Instagram|Line\//i.test(ua)
  }, [])

  function InAppBrowserNotice() {
    if (!isInAppBrowser) return null

    return (
      <div
        role="status"
        style={{
          margin: '0 auto 14px',
          padding: '12px 14px',
          border: '1px solid #dbe5ff',
          borderRadius: '14px',
          background: '#f7f9ff',
          fontSize: '0.92rem',
          lineHeight: 1.55,
          textAlign: 'left',
        }}
      >
        <strong>{t('⚠️ 建議使用瀏覽器開啟', '⚠️ Open in your browser')}</strong>
        <div>
          {t(
            '為了正常播放發音與使用練習功能，請使用 Chrome 或 Safari 開啟 EnSound UP。',
            'For reliable audio and practice features, please open EnSound UP in Chrome or Safari.',
          )}
        </div>
      </div>
    )
  }

  function changeUiLang(lang: 'zh-TW' | 'en') {
    setUiLang(lang)
    window.localStorage.setItem('ensound-ui-lang', lang)
  }

  function tryUseRepeat() {
    if (isFull) return true

    if (freeUsage.repeat >= 3) {
      setPaywallReason('repeat')
      return false
    }

    setFreeUsage((current) => ({
      ...current,
      repeat: current.repeat + 1,
    }))
    return true
  }

function renderPaywall() {
  if (!paywallReason) return null

  return (
    <div className="paywall-card">
      <div className="paywall-badge">EnSound UP Full</div>

      <h3>
        {t('免費體驗已用完', 'Free trial limit reached')}
      </h3>

      <p>
        {paywallReason === 'repeat' &&
          t(
            '免費版提供 3 次 Repeat 體驗。升級完整版後即可使用無限 Repeat ♾️',
            'Free includes 3 Repeat trials. Upgrade to Full for unlimited Repeat ♾️',
          )}

        {paywallReason === 'questions' &&
          t(
            '免費版提供 3 題體驗。升級完整版即可繼續挑戰。',
            'Free includes 3 trial questions. Upgrade to Full to continue.',
          )}
      </p>

      <div className="paywall-actions">
        <button
          type="button"
          className="paywall-primary"
          onClick={() => {
            // Payment flow will be connected later.
          }}
        >
          {t('解鎖完整版', 'Unlock Full')}
        </button>

        <button
          type="button"
          className="paywall-secondary"
          onClick={() => setPaywallReason(null)}
        >
          {t('稍後再說', 'Maybe later')}
        </button>
      </div>
    </div>
  )
}

  const [screen, setScreen] = useState<'sentence' | 'vowel' | 'vowelBasics' | 'consonant' | 'consonantTest' | 'contrast' | 'audioqa' | 'level2proto' | 'choose2'>('contrast')
  const [selectedSoundLetter, setSelectedSoundLetter] = useState<SoundMapLetter>('A')
  const [selectedSoundIndex, setSelectedSoundIndex] = useState(0)
  const [consonantPhase, setConsonantPhase] = useState<'compare' | 'challenge' | 'result'>('compare')
  const [consonantPairIndex, setConsonantPairIndex] = useState(0)
  const consonantPair = CONSONANT_PAIRS[consonantPairIndex]
  const consonantSounds = consonantPair.sounds
  const [consonantLooping, setConsonantLooping] = useState(false)
  const [consonantPlayingWord, setConsonantPlayingWord] = useState<string | null>(null)
  const consonantLoopSessionRef = useRef(0)
  const [consonantQuestion, setConsonantQuestion] = useState(0)
  const [consonantScore, setConsonantScore] = useState(0)
  const [consonantTarget, setConsonantTarget] = useState<0 | 1>(0)
  const [consonantListened, setConsonantListened] = useState(false)
  const [consonantAnswer, setConsonantAnswer] = useState<0 | 1 | null>(null)
  const [consonantTestQuestions, setConsonantTestQuestions] = useState<ConsonantTestQuestion[]>([])
  const [consonantTestIndex, setConsonantTestIndex] = useState(0)
  const [consonantTestAnswer, setConsonantTestAnswer] = useState<string | null>(null)
  const [consonantTestListened, setConsonantTestListened] = useState(false)
  const [consonantTestScore, setConsonantTestScore] = useState(0)
  const [consonantTestMisses, setConsonantTestMisses] = useState<number[]>([])
  const [contrastStageIndex, setContrastStageIndex] = useState(0)
  const [contrastPairIndex, setContrastPairIndex] = useState(0)
  const [contrastRepeatSide, setContrastRepeatSide] = useState<'left' | 'right' | null>(null)
  const contrastRepeatRef = useRef<'left' | 'right' | null>(null)
  const [isABLooping, setIsABLooping] = useState(false)
  const [loopPlayingWord, setLoopPlayingWord] = useState<string | null>(null)
  const abLoopRef = useRef(false)
  const [isQaLooping, setIsQaLooping] = useState(false)
  const [qaPlayingWord, setQaPlayingWord] = useState<string | null>(null)
  const qaLoopRef = useRef(false)
  const [contrastPhase, setContrastPhase] = useState<'learn' | 'challenge' | 'result'>('learn')
  const [challengeTarget, setChallengeTarget] = useState<'left' | 'right'>('left')
  const [challengeAnswer, setChallengeAnswer] = useState<'left' | 'right' | null>(null)
  const [challengeQuestion, setChallengeQuestion] = useState(0)
  const [challengeScore, setChallengeScore] = useState(0)
  const [hasListened, setHasListened] = useState(false)
  const [isASoundRepeating, setIsASoundRepeating] = useState(false)
  const aSoundRepeatRef = useRef(false)
  const soundRepeatSessionRef = useRef(0)
  const [sentence, setSentence] = useState(DEFAULT_SENTENCE)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(5)
  const repeatTimerRef = useRef<number | null>(null)
  const [isSentenceRepeating, setIsSentenceRepeating] = useState(false)
  const [pronunciation, setPronunciation] = useState<WordPronunciation | null>(null)
  const [pronunciationLoading, setPronunciationLoading] = useState(false)
  const [selectedPronunciationIndex, setSelectedPronunciationIndex] = useState(0)
  const [showPronunciations, setShowPronunciations] = useState(false)
  const [freeChosen, setFreeChosen] = useState<string[]>(['æ','ɛ'])
  const [freeStage, setFreeStage] = useState<0|1|2>(0)
  const [diagQuestion,setDiagQuestion]=useState(0)
  const [diagTarget,setDiagTarget]=useState(0)
  const [diagAnswer,setDiagAnswer]=useState<number|null>(null)
  const [diagListened,setDiagListened]=useState(false)
  const [diagStarted,setDiagStarted]=useState(false)
  const [diagMistakes,setDiagMistakes]=useState<Record<string,{count:number,chosen:Record<string,number>}>>({})
  const [diagScore,setDiagScore]=useState(0)
  const [diagChoices,setDiagChoices]=useState<TestBankItem[]>([])

  const words = useMemo(() => tokenizeSentence(sentence), [sentence])
  const selectedWord =
    selectedIndex === null ? null : words.find((word) => word.index === selectedIndex) ?? null

  useEffect(() => {
    let cancelled = false

    if (!selectedWord) {
      setPronunciation(null)
      setPronunciationLoading(false)
      return () => {
        cancelled = true
      }
    }

    setPronunciation(null)
    setSelectedPronunciationIndex(0)
    setShowPronunciations(false)
    setPronunciationLoading(true)

    void lookupCmudictWord(selectedWord.spoken)
      .then((result) => {
        if (!cancelled) {
          setPronunciation(result)
        }
      })
      .catch((error) => {
        console.error('Pronunciation lookup failed:', error)
        if (!cancelled) {
          setPronunciation(null)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPronunciationLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedWord?.spoken])

  const primaryPronunciation =
    pronunciation?.pronunciations[selectedPronunciationIndex] ?? null

  function stopRepeat() {
    if (repeatTimerRef.current !== null) {
      window.clearInterval(repeatTimerRef.current)
      repeatTimerRef.current = null
    }
    setIsSentenceRepeating(false)
    window.speechSynthesis?.cancel()
  }

  useEffect(() => {
    if (screen !== 'sentence' && repeatTimerRef.current !== null) stopRepeat()
  }, [screen])

  function handleSentenceChange(value: string) {
    stopRepeat()
    setSentence(value)
    const selectableWords = tokenizeSentence(value)
    setSelectedIndex(selectableWords.length === 1 ? selectableWords[0].index : null)
  }

  function handleSelect(index: number) {
    stopRepeat()
    setSelectedIndex(index)
  }

  function handlePlay(rate: number) {
    if (!selectedWord) return
    stopRepeat()
    speakWord(selectedWord.spoken, rate)
  }

function handleRepeat() {
  if (!selectedWord) return
  if (repeatTimerRef.current !== null) {
    stopRepeat()
    return
  }

if (!tryUseRepeat()) return

  stopRepeat()
  speakWord(selectedWord.spoken, 0.9)
  repeatTimerRef.current = window.setInterval(() => {
    speakWord(selectedWord.spoken, 0.9)
  }, 1800)
  setIsSentenceRepeating(true)
}

  function highlightSoundMapLetter(word: string) {
    const index = word.toLowerCase().indexOf(selectedSoundLetter.toLowerCase())

    if (index < 0) {
      return word
    }

    return (
      <>
        {word.slice(0, index)}
        <span className="target-letter">{word[index]}</span>
        {word.slice(index + 1)}
      </>
    )
  }

  function stopASoundRepeat() {
    aSoundRepeatRef.current = false
    soundRepeatSessionRef.current += 1
    setIsASoundRepeating(false)
    window.speechSynthesis.cancel()
  }

  function toggleASoundRepeat() {
    if (aSoundRepeatRef.current) {
      stopASoundRepeat()
      return
    }

    if (!tryUseRepeat()) return

    aSoundRepeatRef.current = true
    const session = ++soundRepeatSessionRef.current
    setIsASoundRepeating(true)

    const loop = () => {
      if (!aSoundRepeatRef.current || soundRepeatSessionRef.current !== session) return

      const utterance = new SpeechSynthesisUtterance(selectedSound.word)
      utterance.rate = 0.82
      const googleUsVoice = window.speechSynthesis
        .getVoices()
        .find(
          (voice) =>
            voice.name === 'Google US English' &&
            voice.lang === 'en-US',
        )

      if (googleUsVoice) {
        utterance.voice = googleUsVoice
      }
      utterance.onend = () => {
        if (aSoundRepeatRef.current && soundRepeatSessionRef.current === session) {
          window.setTimeout(loop, 350)
        }
      }
      window.speechSynthesis.speak(utterance)
    }

    window.speechSynthesis.cancel()
    loop()
  }

  const currentSounds = SOUND_MAP[selectedSoundLetter]
  const selectedSound = currentSounds[selectedSoundIndex]

  function playSoundMapWord(index = selectedSoundIndex, rate = 0.82) {
    speakWord(currentSounds[index].word, rate)
  }

  function stopConsonantLoop() {
    consonantLoopSessionRef.current += 1
    setConsonantLooping(false)
    setConsonantPlayingWord(null)
    window.speechSynthesis.cancel()
  }

  function selectConsonantPair(index: number) {
    stopConsonantLoop()
    setConsonantPairIndex(index)
    setConsonantPhase('compare')
    setConsonantQuestion(0)
    setConsonantScore(0)
    setConsonantListened(false)
    setConsonantAnswer(null)
  }

  function toggleConsonantLoop() {
    if (consonantLooping) {
      stopConsonantLoop()
      return
    }
    if (!tryUseRepeat()) return

    stopConsonantLoop()
    const session = consonantLoopSessionRef.current
    setConsonantLooping(true)
    const playAt = (index: 0 | 1) => {
      if (consonantLoopSessionRef.current !== session) return
      const item = consonantSounds[index]
      setConsonantPlayingWord(item.word)
      const utterance = new SpeechSynthesisUtterance(item.word)
      utterance.lang = 'en-US'
      utterance.rate = 0.82
      const googleUsVoice = window.speechSynthesis.getVoices().find(
        (voice) => voice.name === 'Google US English' && voice.lang === 'en-US',
      )
      if (googleUsVoice) utterance.voice = googleUsVoice
      utterance.onend = () => {
        if (consonantLoopSessionRef.current === session) {
          window.setTimeout(() => playAt(index === 0 ? 1 : 0), 420)
        }
      }
      utterance.onerror = () => {
        if (consonantLoopSessionRef.current === session) stopConsonantLoop()
      }
      window.speechSynthesis.speak(utterance)
    }
    playAt(0)
  }

  useEffect(() => {
    if (screen !== 'consonant' && consonantLooping) stopConsonantLoop()
  }, [screen, consonantLooping])

  const contrastStage = LEVEL1_STAGES[contrastStageIndex]
  const contrastPair = contrastStage.pairs[contrastPairIndex % contrastStage.pairs.length]

  function stopContrastRepeat() {
    contrastRepeatRef.current = null
    setContrastRepeatSide(null)
    abLoopRef.current = false
    setIsABLooping(false)
    setLoopPlayingWord(null)
    window.speechSynthesis.cancel()
  }

  function toggleABLoop() {
    if (abLoopRef.current) {
      stopContrastRepeat()
      return
    }

    if (!tryUseRepeat()) return

    stopContrastRepeat()
    abLoopRef.current = true
    setIsABLooping(true)

    const loopItems =
      contrastStage.id === '1C'
        ? contrastStage.pairs.flatMap((pair) => [pair.left, pair.right])
        : [contrastPair.left, contrastPair.right]

    const playAt = (index: number) => {
      if (!abLoopRef.current) return

      const item = loopItems[index]
      setLoopPlayingWord(item.word)
      const utterance = new SpeechSynthesisUtterance(item.word)
      utterance.lang = 'en-US'
      utterance.rate = 0.82

      const googleUsVoice = window.speechSynthesis.getVoices().find(
        (voice) => voice.name === 'Google US English' && voice.lang === 'en-US',
      )
      if (googleUsVoice) utterance.voice = googleUsVoice

      utterance.onend = () => {
        if (!abLoopRef.current) return
        window.setTimeout(
          () => playAt((index + 1) % loopItems.length),
          420,
        )
      }
      utterance.onerror = () => {
        if (abLoopRef.current) stopContrastRepeat()
      }

      window.speechSynthesis.speak(utterance)
    }

    playAt(0)
  }

  function startContrastRepeat(side: 'left' | 'right') {
    if (contrastRepeatRef.current === side) {
      stopContrastRepeat()
      return
    }

    if (!tryUseRepeat()) return

    window.speechSynthesis.cancel()
    contrastRepeatRef.current = side
    setContrastRepeatSide(side)

    const playAgain = () => {
      if (contrastRepeatRef.current !== side) return
      const utterance = new SpeechSynthesisUtterance(contrastPair[side].word)
      utterance.lang = 'en-US'
      utterance.rate = 0.82
      const googleUsVoice = window.speechSynthesis.getVoices().find(
        (voice) => voice.name === 'Google US English' && voice.lang === 'en-US',
      )
      if (googleUsVoice) utterance.voice = googleUsVoice
      utterance.onend = () => {
        if (contrastRepeatRef.current === side) {
          window.setTimeout(playAgain, 280)
        }
      }
      window.speechSynthesis.speak(utterance)
    }

    playAgain()
  }


function startChallenge() {
  if (!isFull && freeUsage.questions >= 3) {
    setPaywallReason('questions')
    return
  }

  posthog.capture('practice_started', {
    mode: 'guided',
    stage: contrastStage.id,
  })

  stopASoundRepeat()
  stopContrastRepeat()
  setChallengeQuestion(0)
  setChallengeScore(0)
  setChallengeAnswer(null)
  setHasListened(false)
  setChallengeTarget(Math.random() < 0.5 ? 'left' : 'right')
  setContrastPhase('challenge')
}

function answerChallenge(side: 'left' | 'right') {
  if (challengeAnswer !== null) return

  if (!isFull) {
    setFreeUsage((current) => ({
      ...current,
      questions: current.questions + 1,
    }))
  }

  setChallengeAnswer(side)

  if (side === challengeTarget) {
    setChallengeScore((score) => score + 1)
  }
}
function nextChallengeQuestion() {
  if (!isFull && freeUsage.questions >= 3) {
    setPaywallReason('questions')
    return
  }

  if (challengeQuestion >= 5) {
    setContrastPhase('result')
    return
  }

  const next = challengeQuestion + 1
  setChallengeQuestion(next)
  setContrastPairIndex(next % contrastStage.pairs.length)
  setChallengeAnswer(null)
  setHasListened(false)
  setChallengeTarget(Math.random() < 0.5 ? 'left' : 'right')
}

  const CONTRAST_LAB = [
    { id: 'A', left: { vowel: 'æ', word: 'cap', ipa: '/kæp/' }, right: { vowel: 'ʌ', word: 'cup', ipa: '/kʌp/' } },
    { id: 'B', left: { vowel: 'ʌ', word: 'cut', ipa: '/kʌt/' }, right: { vowel: 'ɑ', word: 'cot', ipa: '/kɑt/' } },
    { id: 'C', left: { vowel: 'ʊ', word: 'full', ipa: '/fʊl/' }, right: { vowel: 'uː', word: 'fool', ipa: '/fuːl/' } },
    { id: 'D', left: { vowel: 'iː', word: 'sheep', ipa: '/ʃiːp/' }, right: { vowel: 'ɪ', word: 'ship', ipa: '/ʃɪp/' } },
    { id: 'E', left: { vowel: 'ɑ', word: 'hot', ipa: '/hɑt/' }, right: { vowel: 'ɛ', word: 'head', ipa: '/hɛd/' } },
  ] as const

  const [qaPairIndex, setQaPairIndex] = useState(0)
  const [l2Phase, setL2Phase] = useState<'learn' | 'challenge' | 'result'>('learn')
  const [l2Looping, setL2Looping] = useState(false)
  const [l2PlayingWord, setL2PlayingWord] = useState<string | null>(null)
  const l2LoopRef = useRef(false)
  const [l2Question, setL2Question] = useState(0)
  const [l2Score, setL2Score] = useState(0)
  const [l2TargetIndex, setL2TargetIndex] = useState(0)
  const [l2AnswerIndex, setL2AnswerIndex] = useState<number | null>(null)
  const [l2HasListened, setL2HasListened] = useState(false)
  const [choosePairIndex, setChoosePairIndex] = useState(0)
  const [choosePhase, setChoosePhase] = useState<'compare' | 'challenge' | 'result'>('compare')
  const [chooseLooping, setChooseLooping] = useState(false)
  const [choosePlayingWord, setChoosePlayingWord] = useState<string | null>(null)
  const chooseLoopRef = useRef(false)
  const [chooseQuestion, setChooseQuestion] = useState(0)
  const [chooseScore, setChooseScore] = useState(0)
  const [chooseTarget, setChooseTarget] = useState<0 | 1>(0)
  const [chooseAnswer, setChooseAnswer] = useState<0 | 1 | null>(null)
  const [chooseHasListened, setChooseHasListened] = useState(false)
  const [chooseChallengePairIndex, setChooseChallengePairIndex] = useState(0)

  function stopQaLoop() {
    qaLoopRef.current = false
    setIsQaLooping(false)
    setQaPlayingWord(null)
    window.speechSynthesis.cancel()
  }

  function playQaWord(word: string) {
    stopQaLoop()
    setQaPlayingWord(word)
    const utterance = new SpeechSynthesisUtterance(word)
    utterance.lang = 'en-US'
    utterance.rate = 0.9
    utterance.pitch = 1
    utterance.onend = () => setQaPlayingWord(null)
    utterance.onerror = () => setQaPlayingWord(null)
    window.speechSynthesis.speak(utterance)
  }

  function toggleQaLoop() {
    if (qaLoopRef.current) {
      stopQaLoop()
      return
    }

    if (!tryUseRepeat()) return

    stopContrastRepeat()
    stopASoundRepeat()
    window.speechSynthesis.cancel()

    const pair = CONTRAST_LAB[qaPairIndex]
    const items = [pair.left.word, pair.right.word]
    qaLoopRef.current = true
    setIsQaLooping(true)

    const playAt = (index: number) => {
      if (!qaLoopRef.current) return
      const word = items[index]
      setQaPlayingWord(word)

      const utterance = new SpeechSynthesisUtterance(word)
      utterance.lang = 'en-US'
      utterance.rate = 0.9
      utterance.pitch = 1
      utterance.onend = () => {
        if (!qaLoopRef.current) return
        window.setTimeout(() => playAt((index + 1) % items.length), 450)
      }
      utterance.onerror = stopQaLoop
      window.speechSynthesis.speak(utterance)
    }

    playAt(0)
  }

  if (screen === 'audioqa') {
    const pair = CONTRAST_LAB[qaPairIndex]

    return (
      <main className="app-shell">
        <section className="app-card audio-qa-card">
          <header className="header">
            <div>
              <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div>
              <p className="tagline">Level 1 Lab — test two-vowel contrasts before we build the course.</p>
            </div>
          </header>
          <MainNav active="lab" />
          <InAppBrowserNotice />
          <ProductInfo />

          <p className="eyebrow">Step 3D-2 · L1 Lab</p>
          <h1 className="contrast-title">Level 1 · Two Vowels</h1>
          <p className="contrast-subtitle">Two vowels at a time. Prefer the same consonant frame; near-consonant pairs stay experimental.</p>

          <div className="qa-pair-tabs">
            {CONTRAST_LAB.map((item, index) => (
              <button
                key={item.id}
                className={qaPairIndex === index ? 'active' : ''}
                onClick={() => {
                  stopQaLoop()
                  setQaPairIndex(index)
                }}
              >
                /{item.left.vowel}/ ↔ /{item.right.vowel}/
              </button>
            ))}
          </div>

          {pair.id === 'E' && (
            <div className="qa-experimental-note">
              Experimental · near consonants, not a pure same-frame minimal pair
            </div>
          )}

          <div className="qa-pair-grid">
            {[pair.left, pair.right].map((item) => (
              <button
                key={item.word}
                className={`qa-word-card ${qaPlayingWord === item.word ? 'playing' : ''}`}
                onClick={() => playQaWord(item.word)}
              >
                <span className="qa-vowel">/{item.vowel}/</span>
                <strong>{item.word.toUpperCase()}</strong>
                <span>{item.ipa}</span>
                <VowelWordMeaning word={item.word} />
                <small>🔊 {t('播放','Listen')}</small>
              </button>
            ))}
          </div>

          <div className="loop-control-panel compact qa-loop">
            <div className="loop-word-indicators">
              {[pair.left, pair.right].map((item) => (
                <span key={item.word} className={qaPlayingWord === item.word ? 'playing' : ''}>
                  {item.word.toUpperCase()}{qaPlayingWord === item.word && <b> 🔊</b>}
                </span>
              ))}
            </div>
            <button className={`ab-loop-button ${isQaLooping ? 'active' : ''}`} onClick={toggleQaLoop}>
              {isQaLooping ? t('■ 停止循環','■ Stop Loop') : `∞ Loop ${pair.left.word.toUpperCase()} ↔ ${pair.right.word.toUpperCase()}`}
            </button>
          </div>

          <div className="qa-judge">
            <span>After listening, judge it yourself:</span>
            <strong>Too easy · Close · Easy to confuse</strong>
          </div>

          <p className="qa-note">
            A–D use tightly controlled consonant frames. HOT / HEAD is marked experimental because the final consonant also changes.
          </p>

          {paywallReason === 'repeat' && renderPaywall()}
        </section>
        <SiteFooter />
    </main>
    )
  }



  const CHOOSE2_PAIRS = [
    { label: '/æ/ ↔ /ɛ/', left: { vowel: 'æ', word: 'bat', ipa: '/bæt/' }, right: { vowel: 'ɛ', word: 'bet', ipa: '/bɛt/' }, note: 'Same frame /b_t/' },
    { label: '/æ/ ↔ /ʌ/', left: { vowel: 'æ', word: 'cap', ipa: '/kæp/' }, right: { vowel: 'ʌ', word: 'cup', ipa: '/kʌp/' }, note: 'Same frame /k_p/' },
    { label: '/ʌ/ ↔ /ɑ/', left: { vowel: 'ʌ', word: 'cut', ipa: '/kʌt/' }, right: { vowel: 'ɑ', word: 'cot', ipa: '/kɑt/' }, note: 'Same frame /k_t/' },
    { label: '/ʊ/ ↔ /uː/', left: { vowel: 'ʊ', word: 'full', ipa: '/fʊl/' }, right: { vowel: 'uː', word: 'fool', ipa: '/fuːl/' }, note: 'Same frame /f_l/' },
    { label: '/iː/ ↔ /ɪ/', left: { vowel: 'iː', word: 'sheep', ipa: '/ʃiːp/' }, right: { vowel: 'ɪ', word: 'ship', ipa: '/ʃɪp/' }, note: 'Same frame /ʃ_p/' },
    { label: '/ɑ/ ↔ /ɛ/', left: { vowel: 'ɑ', word: 'hot', ipa: '/hɑt/' }, right: { vowel: 'ɛ', word: 'head', ipa: '/hɛd/' }, note: 'Experimental · near consonants' },
  ] as const

  function stopChooseLoop() {
    chooseLoopRef.current = false
    setChooseLooping(false)
    setChoosePlayingWord(null)
    window.speechSynthesis.cancel()
  }

  function speakChoose(word: string) {
    window.speechSynthesis.cancel()
    setChoosePlayingWord(word)
    const u = new SpeechSynthesisUtterance(word)
    u.lang = 'en-US'
    u.rate = 0.9
    u.pitch = 1
    u.onend = () => setChoosePlayingWord(null)
    u.onerror = () => setChoosePlayingWord(null)
    window.speechSynthesis.speak(u)
  }

  function toggleChooseLoop() {
    if (chooseLoopRef.current) {
      stopChooseLoop()
      return
    }
    if (!tryUseRepeat()) return
    stopQaLoop()
    stopL2Loop()
    stopContrastRepeat()
    stopASoundRepeat()
    const pair = CHOOSE2_PAIRS[choosePairIndex]
    const words = [pair.left.word, pair.right.word]
    chooseLoopRef.current = true
    setChooseLooping(true)
    const playAt = (i: number) => {
      if (!chooseLoopRef.current) return
      const word = words[i]
      setChoosePlayingWord(word)
      const u = new SpeechSynthesisUtterance(word)
      u.lang = 'en-US'
      u.rate = 0.9
      u.pitch = 1
      u.onend = () => {
        if (chooseLoopRef.current) window.setTimeout(() => playAt((i + 1) % 2), 450)
      }
      u.onerror = stopChooseLoop
      window.speechSynthesis.speak(u)
    }
    playAt(0)
  }

  function startChooseChallenge() {
    posthog.capture('practice_started', {
      mode: 'choose_two',
      pair: freeChosen.map(v => `/${v}/`).join(' + '),
    })
    stopChooseLoop()
    setChooseQuestion(0)
    setChooseScore(0)
    setChooseAnswer(null)
    setChooseHasListened(false)
    setChooseTarget(Math.random() < .5 ? 0 : 1)
    setChoosePhase('challenge')
  }

  function playChooseTarget() {
    const pair = CHOOSE2_PAIRS[choosePairIndex]
    const item = chooseTarget === 0 ? pair.left : pair.right
    setChooseHasListened(true)
    speakChoose(item.word)
  }

  function answerChoose(i: 0 | 1) {
    if (!chooseHasListened || chooseAnswer !== null) return
    setChooseAnswer(i)
    if (i === chooseTarget) setChooseScore(s => s + 1)
  }

  function nextChooseQuestion() {
    if (chooseQuestion >= 5) {
      setChoosePhase('result')
      return
    }
    setChooseQuestion(q => q + 1)
    setChooseAnswer(null)
    setChooseHasListened(false)
    setChooseTarget(Math.random() < .5 ? 0 : 1)
  }


  function ProductInfo() {
    if (!showProductInfo) return null

    return (
      <div
        role="presentation"
        onClick={() => setShowProductInfo(false)}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1000,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '20px',
          background: 'rgba(20, 38, 86, 0.18)',
          backdropFilter: 'blur(3px)',
        }}
      >
        <section
          role="dialog"
          aria-modal="true"
          aria-label={t('EnSound UP 商品資訊', 'EnSound UP product information')}
          onClick={(event) => event.stopPropagation()}
          style={{
            width: 'min(460px, 100%)',
            padding: '24px',
            border: '1px solid #dbe5ff',
            borderRadius: '20px',
            background: '#fff',
            boxShadow: '0 18px 50px rgba(28, 53, 120, 0.16)',
            textAlign: 'center',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              type="button"
              onClick={() => setShowProductInfo(false)}
              aria-label={t('關閉', 'Close')}
              style={{ border: 0, background: 'transparent', cursor: 'pointer', fontSize: '1.25rem' }}
            >
              ×
            </button>
          </div>
          <p className="eyebrow" style={{ marginBottom: '6px' }}>
            {t('EnSound UP Web 完整版', 'EnSound UP Web Full')}
          </p>
          <h2 style={{ margin: '0 0 12px' }}>
            {t('英文發音與母音辨識練習工具', 'English pronunciation and vowel listening practice')}
          </h2>
          <p style={{ margin: '0 0 8px' }}>
            {t('正式售價 ', 'Regular price ')}<strong>NT$199</strong>
          </p>
          <p style={{ margin: '0 0 12px' }}>
            <strong>{t('首發限量 50 份 NT$99', 'Launch offer · first 50 purchases NT$99')}</strong>
          </p>
          <p style={{ margin: '0 0 18px' }}>
            {t(
              '一次買斷 EnSound UP Web 完整版。基礎聆聽功能免費使用；完整版提供完整練習與無限 Repeat。',
              'One-time purchase for EnSound UP Web Full. Basic listening stays free; Full unlocks complete practice and unlimited Repeat.',
            )}
          </p>

          <div
            style={{
              margin: '0 0 18px',
              padding: '16px',
              borderRadius: '14px',
              background: '#f7f9ff',
              textAlign: 'left',
            }}
          >
            <p style={{ margin: '0 0 5px', fontWeight: 700 }}>
              {t('付款方式', 'Payment')}
            </p>
            <p style={{ margin: '0 0 14px', fontSize: '0.92rem', lineHeight: 1.6 }}>
              {t(
                '線上付款（正式開放後由綠界科技提供付款服務）',
                'Online payment (payment service will be provided by ECPay after launch).',
              )}
            </p>

            <p style={{ margin: '0 0 5px', fontWeight: 700 }}>
              {t('退款說明', 'Refunds')}
            </p>
            <p style={{ margin: 0, fontSize: '0.92rem', lineHeight: 1.6 }}>
              {t(
                '購買後 7 日內可聯絡客服提出退款申請。退款完成後，完整版使用權將停止。',
                'You may contact support to request a refund within 7 days of purchase. Full access will end after the refund is completed.',
              )}
            </p>
          </div>

          <p style={{ margin: 0, fontSize: '0.92rem' }}>
            {t('客服：', 'Support: ')}
            <a href="mailto:uptools.support@gmail.com">uptools.support@gmail.com</a>
          </p>
        </section>
      </div>
    )
  }


  function LegalModal() {
    if (!legalPage) return null

    const content = {
      terms: {
        title: t('服務條款', 'Terms of Service'),
        body: isZh ? (
          <>
            <p>EnSound UP 是由 UP Tools 提供的英文發音與聽辨練習 Web 工具。使用本服務即表示您同意依本條款使用網站與相關功能。</p>
            <h3>購買內容</h3>
            <p>付費商品為「EnSound UP Web 完整版」，採一次買斷。購買後可使用目前 Web 完整版所提供的完整練習與無限 Repeat 等付費功能。</p>
            <p>本次 Web 完整版購買不代表包含未來 App、其他 UP Tools 產品、獨立新服務或所有未來重大版本；若未來推出上述產品或服務，可能另行販售。</p>
            <h3>使用與維護</h3>
            <p>UP Tools 可能為維護、安全、錯誤修正或改善使用體驗而更新本服務。使用者不得以違法方式使用本服務，亦不得未經授權重製或散布受保護的內容。</p>
            <h3>聯絡方式</h3>
            <p>如有購買、使用或帳號相關問題，請聯絡 uptools.support@gmail.com。</p>
          </>
        ) : (
          <>
            <p>EnSound UP is a web-based English pronunciation and listening practice tool provided by UP Tools. By using the service, you agree to use the website and its features in accordance with these terms.</p>
            <h3>Purchase</h3>
            <p>The paid product is EnSound UP Web Full, offered as a one-time purchase. It unlocks the paid features included in the current Web Full version, including complete practice and unlimited Repeat.</p>
            <p>This Web purchase does not include future apps, other UP Tools products, separate new services, or every future major version. Those may be sold separately.</p>
            <h3>Use and maintenance</h3>
            <p>UP Tools may update the service for maintenance, security, bug fixes, or user-experience improvements. The service may not be used unlawfully, and protected content may not be reproduced or distributed without authorization.</p>
            <h3>Contact</h3>
            <p>For purchase, access, or account questions, contact uptools.support@gmail.com.</p>
          </>
        ),
      },
      refund: {
        title: t('退款政策', 'Refund Policy'),
        body: isZh ? (
          <>
            <p>EnSound UP Web 完整版提供購買後 7 日內申請退款的服務政策。</p>
            <h3>如何申請</h3>
            <p>請於購買後 7 日內寄信至 uptools.support@gmail.com，並提供足以確認訂單的購買 Email 或相關訂單資訊。請勿以 Email 傳送信用卡完整卡號或其他不必要的敏感資料。</p>
            <h3>退款完成後</h3>
            <p>退款完成後，該筆購買的 EnSound UP Web 完整版使用權將停止。若日後重新購買，將依重新購買當時的售價與活動條件計算。</p>
            <p>本退款政策不限制依法不得排除或限制的消費者權利。</p>
          </>
        ) : (
          <>
            <p>EnSound UP Web Full offers a service policy allowing refund requests within 7 days of purchase.</p>
            <h3>How to request a refund</h3>
            <p>Email uptools.support@gmail.com within 7 days of purchase and provide the purchase email or order information needed to identify the transaction. Do not email full card numbers or other unnecessary sensitive information.</p>
            <h3>After a refund</h3>
            <p>Once the refund is completed, access associated with that EnSound UP Web Full purchase will end. A later repurchase will use the price and promotion available at that time.</p>
            <p>This policy does not limit consumer rights that cannot legally be excluded or restricted.</p>
          </>
        ),
      },
      privacy: {
        title: t('隱私權政策', 'Privacy Policy'),
        body: isZh ? (
          <>
            <p>UP Tools 以資料最小化為原則。EnSound UP 的基本練習資料與免費使用額度目前主要儲存在您的瀏覽器本機。</p>
            <h3>網站分析</h3>
            <p>本網站使用 PostHog 協助了解網站造訪與部分功能使用情形，以改善產品。分析資料可能包含瀏覽器、裝置、作業系統、概略地區及產品互動事件等技術資訊。</p>
            <h3>購買與帳號資料</h3>
            <p>當購買與帳號功能正式啟用後，為處理付款、授權、登入與客服，可能需要處理購買 Email、訂單狀態與必要的裝置授權資料。付款資料將由付款服務提供者依其服務流程處理；UP Tools 不要求您透過客服提供完整信用卡資料。</p>
            <h3>聯絡我們</h3>
            <p>如對隱私或資料處理有疑問，請聯絡 uptools.support@gmail.com。</p>
          </>
        ) : (
          <>
            <p>UP Tools follows a data-minimization approach. EnSound UP currently keeps basic practice data and free-usage limits primarily in your browser's local storage.</p>
            <h3>Website analytics</h3>
            <p>This website uses PostHog to understand visits and selected product interactions so we can improve the service. Analytics may include technical information such as browser, device, operating system, approximate region, and product interaction events.</p>
            <h3>Purchase and account data</h3>
            <p>When purchase and account features are enabled, we may process purchase email, order status, and necessary device-authorization data to provide payment, access, sign-in, and support. Payment information is handled through the payment provider's flow; UP Tools does not ask you to send full card details to support.</p>
            <h3>Contact</h3>
            <p>For privacy or data-handling questions, contact uptools.support@gmail.com.</p>
          </>
        ),
      },
    }[legalPage]

    return (
      <div className="legal-modal-backdrop" role="presentation" onClick={() => setLegalPage(null)}>
        <section className="legal-modal" role="dialog" aria-modal="true" aria-label={content.title} onClick={(event) => event.stopPropagation()}>
          <div className="legal-modal-header">
            <div>
              <p className="eyebrow">UP Tools · EnSound UP</p>
              <h2>{content.title}</h2>
            </div>
            <button type="button" className="legal-close" onClick={() => setLegalPage(null)} aria-label={t('關閉', 'Close')}>×</button>
          </div>
          <div className="legal-modal-body">{content.body}</div>
        </section>
      </div>
    )
  }

  function SiteFooter() {
    return (
      <>
        <footer className="legal-footer">
          <span>© 2026 UP Tools</span>
          <nav aria-label={t('網站政策', 'Site policies')}>
            <button type="button" onClick={() => setLegalPage('terms')}>{t('服務條款', 'Terms')}</button>
            <span aria-hidden="true">｜</span>
            <button type="button" onClick={() => setLegalPage('refund')}>{t('退款政策', 'Refund Policy')}</button>
            <span aria-hidden="true">｜</span>
            <button type="button" onClick={() => setLegalPage('privacy')}>{t('隱私權政策', 'Privacy Policy')}</button>
            <span aria-hidden="true">｜</span>
            <a href="mailto:uptools.support@gmail.com">{t('聯絡我們', 'Contact')}</a>
          </nav>
        </footer>
        <LegalModal />
      </>
    )
  }

  function MainNav({ active }: { active: 'sentence' | 'vowel' | 'vowelBasics' | 'consonant' | 'consonantTest' | 'guided' | 'choose2' | 'lab' | 'level2' }) {
    return (
    <div className="mode-switch">
      <button className={`mode-button ${active === 'sentence' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat(); setScreen('sentence')
      }}>{t('單字 / 句子', 'Sentence')}</button>
      <button className={`mode-button ${active === 'vowel' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); stopContrastRepeat(); setScreen('vowel')
      }}>{t('母音發音', 'Vowel Sounds')}</button>
      <button className={`mode-button ${active === 'vowelBasics' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat(); setScreen('vowelBasics')
      }}>{t('母音核心說明', 'Vowel Basics')}</button>
      <button className={`mode-button ${active === 'guided' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); setContrastPhase('learn'); setScreen('contrast')
      }}>{t('母音練習導引', 'Vowel Guided Practice')}</button>
      <button className={`mode-button ${active === 'choose2' ? 'active' : ''}`} onClick={() => {
        stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat(); setChoosePhase('compare'); setScreen('choose2')
      }}>{t('母音練習：自由選 2 音', 'Vowel Choose 2')}</button>
      <button className={`mode-button ${active === 'level2' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopContrastRepeat(); stopASoundRepeat(); setL2Phase('learn'); setScreen('level2proto')
      }}>{t('母音測驗', 'Vowel Test')}</button>
      <button className={`mode-button ${active === 'consonant' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat(); setConsonantPhase('compare'); setScreen('consonant')
      }}>{t('子音練習', 'Consonant Practice')}</button>
      <button className={`mode-button ${active === 'consonantTest' ? 'active' : ''}`} onClick={() => {
        stopChooseLoop(); stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat(); stopConsonantLoop(); setConsonantTestQuestions([]); setPaywallReason(null); setScreen('consonantTest')
      }}>{t('子音測驗', 'Consonant Test')}</button>
    </div>
    )
  }

  const CHOOSE2_LIBRARY = [
    { vowels: ['æ','ɛ'] as const, pairs: [
      { left:{vowel:'æ',word:'bat',ipa:'/bæt/'}, right:{vowel:'ɛ',word:'bet',ipa:'/bɛt/'} },
      { left:{vowel:'æ',word:'bad',ipa:'/bæd/'}, right:{vowel:'ɛ',word:'bed',ipa:'/bɛd/'} },
    ]},
    { vowels: ['æ','ɪ'] as const, pairs: [
      { left:{vowel:'æ',word:'bat',ipa:'/bæt/'}, right:{vowel:'ɪ',word:'bit',ipa:'/bɪt/'} },
      { left:{vowel:'æ',word:'bad',ipa:'/bæd/'}, right:{vowel:'ɪ',word:'bid',ipa:'/bɪd/'} },
    ]},
    { vowels: ['æ','ʌ'] as const, pairs: [
      { left:{vowel:'æ',word:'cap',ipa:'/kæp/'}, right:{vowel:'ʌ',word:'cup',ipa:'/kʌp/'} },
      { left:{vowel:'æ',word:'bat',ipa:'/bæt/'}, right:{vowel:'ʌ',word:'but',ipa:'/bʌt/'} },
    ]},
    { vowels: ['æ','ɑ'] as const, pairs: [
      { left:{vowel:'æ',word:'cap',ipa:'/kæp/'}, right:{vowel:'ɑ',word:'cop',ipa:'/kɑp/'} },
      { left:{vowel:'æ',word:'sack',ipa:'/sæk/'}, right:{vowel:'ɑ',word:'sock',ipa:'/sɑk/'} },
    ]},
    { vowels: ['æ','ʊ'] as const, pairs: [
      { left:{vowel:'æ',word:'pat',ipa:'/pæt/'}, right:{vowel:'ʊ',word:'put',ipa:'/pʊt/'} },
      { left:{vowel:'æ',word:'fat',ipa:'/fæt/'}, right:{vowel:'ʊ',word:'foot',ipa:'/fʊt/'} },
    ]},
    { vowels: ['æ','uː'] as const, pairs: [
      { left:{vowel:'æ',word:'bat',ipa:'/bæt/'}, right:{vowel:'uː',word:'boot',ipa:'/buːt/'} },
      { left:{vowel:'æ',word:'cap',ipa:'/kæp/'}, right:{vowel:'uː',word:'coop',ipa:'/kuːp/'} },
    ]},
    { vowels: ['æ','iː'] as const, pairs: [
      { left:{vowel:'æ',word:'bat',ipa:'/bæt/'}, right:{vowel:'iː',word:'beat',ipa:'/biːt/'} },
      { left:{vowel:'æ',word:'bad',ipa:'/bæd/'}, right:{vowel:'iː',word:'bead',ipa:'/biːd/'} },
    ]},
    { vowels: ['ɛ','ɪ'] as const, pairs: [
      { left:{vowel:'ɛ',word:'bet',ipa:'/bɛt/'}, right:{vowel:'ɪ',word:'bit',ipa:'/bɪt/'} },
      { left:{vowel:'ɛ',word:'bed',ipa:'/bɛd/'}, right:{vowel:'ɪ',word:'bid',ipa:'/bɪd/'} },
    ]},
    { vowels: ['ɛ','ʌ'] as const, pairs: [
      { left:{vowel:'ɛ',word:'bet',ipa:'/bɛt/'}, right:{vowel:'ʌ',word:'but',ipa:'/bʌt/'} },
      { left:{vowel:'ɛ',word:'bed',ipa:'/bɛd/'}, right:{vowel:'ʌ',word:'bud',ipa:'/bʌd/'} },
    ]},
    { vowels: ['ɛ','ɑ'] as const, pairs: [
      { left:{vowel:'ɛ',word:'adept',ipa:'/əˈdɛpt/'}, right:{vowel:'ɑ',word:'adopt',ipa:'/əˈdɑpt/'} },
      { left:{vowel:'ɛ',word:'edible',ipa:'/ˈɛdəbəl/'}, right:{vowel:'ɑ',word:'audible',ipa:'/ˈɑdəbəl/'} },
    ]},
    { vowels: ['ɛ','ʊ'] as const, pairs: [
      { left:{vowel:'ɛ',word:'pet',ipa:'/pɛt/'}, right:{vowel:'ʊ',word:'put',ipa:'/pʊt/'} },
      { left:{vowel:'ɛ',word:'fell',ipa:'/fɛl/'}, right:{vowel:'ʊ',word:'full',ipa:'/fʊl/'} },
    ]},
    { vowels: ['ɛ','uː'] as const, pairs: [
      { left:{vowel:'ɛ',word:'bet',ipa:'/bɛt/'}, right:{vowel:'uː',word:'boot',ipa:'/buːt/'} },
      { left:{vowel:'ɛ',word:'less',ipa:'/lɛs/'}, right:{vowel:'uː',word:'loose',ipa:'/luːs/'} },
    ]},
    { vowels: ['ɛ','iː'] as const, pairs: [
      { left:{vowel:'ɛ',word:'bet',ipa:'/bɛt/'}, right:{vowel:'iː',word:'beat',ipa:'/biːt/'} },
      { left:{vowel:'ɛ',word:'bed',ipa:'/bɛd/'}, right:{vowel:'iː',word:'bead',ipa:'/biːd/'} },
    ]},
    { vowels: ['ɪ','ʌ'] as const, pairs: [
      { left:{vowel:'ɪ',word:'bit',ipa:'/bɪt/'}, right:{vowel:'ʌ',word:'but',ipa:'/bʌt/'} },
      { left:{vowel:'ɪ',word:'bid',ipa:'/bɪd/'}, right:{vowel:'ʌ',word:'bud',ipa:'/bʌd/'} },
    ]},
    { vowels: ['ɪ','ɑ'] as const, pairs: [
      { left:{vowel:'ɪ',word:'big',ipa:'/bɪɡ/'}, right:{vowel:'ɑ',word:'bog',ipa:'/bɑɡ/'} },
      { left:{vowel:'ɪ',word:'fig',ipa:'/fɪɡ/'}, right:{vowel:'ɑ',word:'fog',ipa:'/fɑɡ/'} },
    ]},
    { vowels: ['ɪ','ʊ'] as const, pairs: [
      { left:{vowel:'ɪ',word:'fit',ipa:'/fɪt/'}, right:{vowel:'ʊ',word:'foot',ipa:'/fʊt/'} },
      { left:{vowel:'ɪ',word:'pit',ipa:'/pɪt/'}, right:{vowel:'ʊ',word:'put',ipa:'/pʊt/'} },
    ]},
    { vowels: ['ɪ','uː'] as const, pairs: [
      { left:{vowel:'ɪ',word:'bit',ipa:'/bɪt/'}, right:{vowel:'uː',word:'boot',ipa:'/buːt/'} },
      { left:{vowel:'ɪ',word:'did',ipa:'/dɪd/'}, right:{vowel:'uː',word:'dude',ipa:'/duːd/'} },
    ]},
    { vowels: ['ɪ','iː'] as const, pairs: [
      { left:{vowel:'ɪ',word:'ship',ipa:'/ʃɪp/'}, right:{vowel:'iː',word:'sheep',ipa:'/ʃiːp/'} },
      { left:{vowel:'ɪ',word:'bit',ipa:'/bɪt/'}, right:{vowel:'iː',word:'beat',ipa:'/biːt/'} },
    ]},
    { vowels: ['ʌ','ɑ'] as const, pairs: [
      { left:{vowel:'ʌ',word:'cut',ipa:'/kʌt/'}, right:{vowel:'ɑ',word:'cot',ipa:'/kɑt/'} },
      { left:{vowel:'ʌ',word:'hut',ipa:'/hʌt/'}, right:{vowel:'ɑ',word:'hot',ipa:'/hɑt/'} },
    ]},
    { vowels: ['ʌ','ʊ'] as const, pairs: [
      { left:{vowel:'ʌ',word:'luck',ipa:'/lʌk/'}, right:{vowel:'ʊ',word:'look',ipa:'/lʊk/'} },
      { left:{vowel:'ʌ',word:'tuck',ipa:'/tʌk/'}, right:{vowel:'ʊ',word:'took',ipa:'/tʊk/'} },
    ]},
    { vowels: ['ʌ','uː'] as const, pairs: [
      { left:{vowel:'ʌ',word:'but',ipa:'/bʌt/'}, right:{vowel:'uː',word:'boot',ipa:'/buːt/'} },
      { left:{vowel:'ʌ',word:'bun',ipa:'/bʌn/'}, right:{vowel:'uː',word:'boon',ipa:'/buːn/'} },
    ]},
    { vowels: ['ʌ','iː'] as const, pairs: [
      { left:{vowel:'ʌ',word:'but',ipa:'/bʌt/'}, right:{vowel:'iː',word:'beat',ipa:'/biːt/'} },
      { left:{vowel:'ʌ',word:'bud',ipa:'/bʌd/'}, right:{vowel:'iː',word:'bead',ipa:'/biːd/'} },
    ]},
    { vowels: ['ɑ','ʊ'] as const, pairs: [
      { left:{vowel:'ɑ',word:'pot',ipa:'/pɑt/'}, right:{vowel:'ʊ',word:'put',ipa:'/pʊt/'} },
      { left:{vowel:'ɑ',word:'god',ipa:'/ɡɑd/'}, right:{vowel:'ʊ',word:'good',ipa:'/ɡʊd/'} },
    ]},
    { vowels: ['ɑ','uː'] as const, pairs: [
      { left:{vowel:'ɑ',word:'bomb',ipa:'/bɑm/'}, right:{vowel:'uː',word:'boom',ipa:'/buːm/'} },
      { left:{vowel:'ɑ',word:'bot',ipa:'/bɑt/'}, right:{vowel:'uː',word:'boot',ipa:'/buːt/'} },
    ]},
    { vowels: ['ɑ','iː'] as const, pairs: [
      { left:{vowel:'ɑ',word:'hot',ipa:'/hɑt/'}, right:{vowel:'iː',word:'heat',ipa:'/hiːt/'} },
      { left:{vowel:'ɑ',word:'otter',ipa:'/ˈɑtɚ/'}, right:{vowel:'iː',word:'eater',ipa:'/ˈiːtɚ/'} },
    ]},
    { vowels: ['ʊ','uː'] as const, pairs: [
      { left:{vowel:'ʊ',word:'full',ipa:'/fʊl/'}, right:{vowel:'uː',word:'fool',ipa:'/fuːl/'} },
      { left:{vowel:'ʊ',word:'pull',ipa:'/pʊl/'}, right:{vowel:'uː',word:'pool',ipa:'/puːl/'} },
    ]},
    { vowels: ['ʊ','iː'] as const, pairs: [
      { left:{vowel:'ʊ',word:'foot',ipa:'/fʊt/'}, right:{vowel:'iː',word:'feet',ipa:'/fiːt/'} },
      { left:{vowel:'ʊ',word:'full',ipa:'/fʊl/'}, right:{vowel:'iː',word:'feel',ipa:'/fiːl/'} },
    ]},
    { vowels: ['uː','iː'] as const, pairs: [
      { left:{vowel:'uː',word:'boot',ipa:'/buːt/'}, right:{vowel:'iː',word:'beat',ipa:'/biːt/'} },
      { left:{vowel:'uː',word:'boon',ipa:'/buːn/'}, right:{vowel:'iː',word:'bean',ipa:'/biːn/'} },
    ]},
    // Controlled single-frame contrasts already used in the comprehensive test.
    { vowels: ['ɔ','aʊ'] as const, pairs: [
      { left:{vowel:'ɔ',word:'all',ipa:'/ɔl/'}, right:{vowel:'aʊ',word:'owl',ipa:'/aʊl/'} },
    ]},
    { vowels: ['eɪ','aɪ'] as const, pairs: [
      { left:{vowel:'eɪ',word:'late',ipa:'/leɪt/'}, right:{vowel:'aɪ',word:'light',ipa:'/laɪt/'} },
    ]},
    { vowels: ['ɔɪ','aɪ'] as const, pairs: [
      { left:{vowel:'ɔɪ',word:'boy',ipa:'/bɔɪ/'}, right:{vowel:'aɪ',word:'buy',ipa:'/baɪ/'} },
    ]},
    { vowels: ['oʊ','aʊ'] as const, pairs: [
      { left:{vowel:'oʊ',word:'no',ipa:'/noʊ/'}, right:{vowel:'aʊ',word:'now',ipa:'/naʊ/'} },
    ]},
  ]

  const CHOOSE2_VOWELS = ['æ','ɛ','ɪ','ʌ','ɑ','ʊ','uː','iː','ɔ','eɪ','aɪ','ɔɪ','oʊ','aʊ'] as const

  function findChooseLibrary() {
    return CHOOSE2_LIBRARY.find(x =>
      x.vowels.every(v => freeChosen.includes(v)) && freeChosen.every(v => x.vowels.includes(v as never))
    )
  }

  if (screen === 'choose2') {
    const lib = findChooseLibrary()
    const pairIndex = freeStage === 1 ? 1 : 0
    const pair = lib?.pairs[pairIndex]
    const loopPairs = lib ? lib.pairs.flatMap(p => [p.left, p.right]) : []
    const displayItems = freeStage === 2 ? loopPairs : pair ? [pair.left,pair.right] : []

    return (
      <main className="app-shell">
        <section className="app-card contrast-practice choose2-real">
          <header className="header">
            <div>
              <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div>
              <p className="tagline">{t('選擇兩個想練習的母音。','Choose the two vowel sounds you want to train.')}</p>
            </div>
          </header>
          <MainNav active="choose2" />
          <InAppBrowserNotice />
          <ProductInfo />

          <p className="eyebrow">{t('自由選 2 音 · 第 1 級','Choose 2 · Level 1')}</p>
          <h1 className="contrast-title">{t('請點選任 2 個音標練習','Pick two vowel sounds')}</h1>
          <p className="contrast-subtitle"></p>

          <div className="free-vowel-picker">
            {CHOOSE2_VOWELS.map(v => {
              const active = freeChosen.includes(v)
              return <button key={v} className={active ? 'active' : ''} onClick={() => {
                stopChooseLoop()
                setChoosePhase('compare')
                setFreeStage(0)
                const nextChosen = freeChosen.includes(v)
                  ? (freeChosen.length > 1 ? freeChosen.filter(x => x !== v) : freeChosen)
                  : (freeChosen.length >= 2 ? [freeChosen[1], v] : [...freeChosen, v])
                setFreeChosen(nextChosen)
                if (nextChosen.length === 2 && nextChosen.join('|') !== freeChosen.join('|')) {
                  posthog.capture('choose_two_pair', {
                    vowel_1: nextChosen[0],
                    vowel_2: nextChosen[1],
                    pair: nextChosen.map(sound => `/${sound}/`).join(' + '),
                  })
                }
              }}>/{v}/</button>
            })}
          </div>

          <p className="selection-line">{t('已選擇：','Selected: ')}<strong>{freeChosen.map(v=>`/${v}/`).join(' + ')}</strong></p>

          {Object.keys(diagMistakes).length > 0 && (() => {
            const recommendations = weakContrastRecommendations(diagMistakes)
            return (
              <section className="needs-practice-board">
                <div className="needs-practice-heading">
                  <div><p className="eyebrow">{t('最近一次母音測驗','Latest Vowel Test')}</p><h2>{t('需要加強', 'Needs Practice')}</h2></div>
                  <span>{recommendations.reduce((sum, item) => sum + item.count, 0)} {t('題答錯','missed')}</span>
                </div>
                <p className="needs-practice-copy">{t('練習你在最近一次測驗中容易混淆的母音。','Practice the contrasts your ear mixed up in the latest test.')}</p>
                <div className="practice-recommendations">
                  {recommendations.map(({target,chosen,count}) => {
                    const ready = CHOOSE2_LIBRARY.some(x => x.vowels.includes(target as never) && x.vowels.includes(chosen as never))
                    return <div className="practice-recommendation" key={`${target}-${chosen}`}>
                      <div><strong>/{target}/ ↔ /{chosen}/</strong><small>{t('建議對比','Practice contrast')} /{target}/ ↔ /{chosen}/ ×{count}</small></div>
                      <button disabled={!ready} onClick={() => {
                        if (!ready) return
                        stopChooseLoop(); setFreeChosen([target,chosen]); setFreeStage(0); setChoosePhase('compare')
                      }}>{ready?t('開始練習 →','Practice →'):t('等待音訊確認','Needs Audio QA')}</button>
                    </div>
                  })}
                </div>
              </section>
            )
          })()}

          {!lib && (
            <div className="pair-unavailable">
              <strong>This pair is not lesson-ready yet.</strong>
              <p>We only enable practice after two same-consonant frames have been mapped and audio-checked.</p>
            </div>
          )}

          {lib && pair && choosePhase === 'compare' && (
            <>
              <div className="level-stage-tabs">
                {[
                  {id:'1A', title:`${lib.pairs[0].left.word.toUpperCase()} vs ${lib.pairs[0].right.word.toUpperCase()}`},
                  ...(lib.pairs.length > 1 ? [
                    {id:'1B', title:`${lib.pairs[1].left.word.toUpperCase()} vs ${lib.pairs[1].right.word.toUpperCase()}`},
                    {id:'1C', title:'Mixed Frames'},
                  ] : []),
                ].map((stage,i) => (
                  <button key={stage.id} className={freeStage===i?'active':''} onClick={()=>{stopChooseLoop();setFreeStage(i as 0|1|2)}}>
                    <strong>{stage.id}</strong><span>{stage.id === '1C' ? t('混合練習','Mixed Frames') : stage.title}</span>
                  </button>
                ))}
              </div>
              <h2 className="contrast-title small">
                {freeStage===2 ? t('混合練習','Mixed Frames') : `${pair.left.word.toUpperCase()} vs ${pair.right.word.toUpperCase()}`}
              </h2>
              <p className="contrast-subtitle">{freeStage===2 ? t('兩組一起混合練習。','Two controlled frames together.') : t('','Same consonants. Only the vowel changes.')}</p>

              <div className="qa-pair-grid">
                {(freeStage===2 ? [lib.pairs[0].left,lib.pairs[0].right] : [pair.left,pair.right]).map(item => (
                  <button key={item.word} className={`qa-word-card ${freeStage!==2 && choosePlayingWord===item.word?'playing':''}`}
                    onClick={()=>{stopChooseLoop();speakChoose(item.word)}}>
                    <span className="qa-vowel">/{item.vowel}/</span>
                    <strong>{item.word.toUpperCase()}</strong><span>{item.ipa}</span><VowelWordMeaning word={item.word} /><small>🔊 {t('播放','Listen')}</small>
                  </button>
                ))}
              </div>

              <div className="loop-control-panel compact">
                <div className="loop-word-indicators">{displayItems.map(item=><span key={item.word} className={choosePlayingWord===item.word?'playing':''}>{item.word.toUpperCase()}{choosePlayingWord===item.word?' 🔊':''}</span>)}</div>
                <button className={`ab-loop-button ${chooseLooping?'active':''}`} onClick={()=>{
                  if (chooseLoopRef.current) { stopChooseLoop(); return }
                  if (!tryUseRepeat()) return
                  stopQaLoop(); stopL2Loop(); stopContrastRepeat(); stopASoundRepeat()
                  chooseLoopRef.current=true; setChooseLooping(true)
                  const words=displayItems.map(x=>x.word)
                  const go=(i:number)=>{
                    if(!chooseLoopRef.current)return
                    const w=words[i]; setChoosePlayingWord(w)
                    const u=new SpeechSynthesisUtterance(w);u.lang='en-US';u.rate=.9
                    u.onend=()=>{if(chooseLoopRef.current)window.setTimeout(()=>go((i+1)%words.length),450)}
                    u.onerror=stopChooseLoop;window.speechSynthesis.speak(u)
                  };go(0)
                }}>{chooseLooping?t('■ 停止循環','■ Stop Loop'):freeStage===2?t('∞ 4 音循環','∞ 4-Sound Loop'):t('∞ A/B 循環','∞ A/B Loop')}</button>
              </div>
<button className="start-challenge-button" onClick={()=>{
  if (!isFull && freeUsage.questions >= 3) {
    setPaywallReason('questions')
    return
  }

  stopChooseLoop()
  setChooseQuestion(0)
  setChooseScore(0)
  setChooseAnswer(null)
  setChooseHasListened(false)
  setChooseTarget(Math.random()<.5?0:1)
  setChooseChallengePairIndex(
    freeStage===2 ? Math.floor(Math.random()*2) : Math.min(freeStage,1)
  )
  setChoosePhase('challenge')
}}>
  {t(
    `開始 ${freeStage===0?'1A':freeStage===1?'1B':'1C'} 的挑戰 →`,
    `Start ${freeStage===0?'1A':freeStage===1?'1B':'1C'} Challenge →`
  )}
</button>

{(paywallReason === 'questions' || paywallReason === 'repeat') && renderPaywall()}
            </>
          )}

          {lib && choosePhase === 'challenge' && (() => {
            const p = lib.pairs[chooseChallengePairIndex]
            const items=[p.left,p.right] as const
            return <div className="l2-challenge">
              <div className="challenge-topbar"><p className="eyebrow">{t('自由選 2 音 · 挑戰','Choose 2 · Challenge')}</p><button className="challenge-exit" onClick={()=>{stopChooseLoop();setChoosePhase('compare')}}>{t('離開 ×','Exit ×')}</button></div>
              <h1 className="contrast-title">{t('你聽到哪一個母音？', 'Which vowel do you hear?')}</h1><p className="question-count">{t('第','Question')} {chooseQuestion+1} / 6 {t('題','')}</p>
              <button className={`challenge-sound choose2-challenge-sound ${chooseHasListened?'played':''}`} onClick={()=>{
                setChooseHasListened(true);speakChoose(items[chooseTarget].word)
              }}>
                <span>🔊</span>
                <strong>{chooseHasListened?t('再播放一次','Play again'):t('先聽聲音','Listen first')}</strong>
              </button>
              <p className="challenge-instruction">{chooseHasListened ? t('請在下列點選你聽到是哪一個音標', 'Now choose the vowel you heard.') : t('先聽才能選擇答案', 'Answers unlock after you listen.')}</p>
              <div className="choose2-answer-grid">{items.map((item,i)=>{
                const answered=chooseAnswer!==null, correct=answered&&i===chooseTarget, wrong=answered&&i===chooseAnswer&&i!==chooseTarget
                return <button key={item.word} disabled={!chooseHasListened} className={`${correct?'correct':''} ${wrong?'wrong':''}`} onClick={()=>{
               if (answered) {
  speakChoose(item.word)
} else {
  if (!isFull) {
    setFreeUsage((current) => ({
      ...current,
      questions: current.questions + 1,
    }))
  }

  setChooseAnswer(i as 0|1)

  if (i===chooseTarget) {
    setChooseScore(s=>s+1)
  }
}
                }}>/{item.vowel}/{answered?' 🔊':''}</button>
              })}</div>
              {chooseAnswer!==null&&<div className={`challenge-feedback ${chooseAnswer===chooseTarget?'correct':'wrong'}`}><strong>{chooseAnswer===chooseTarget?t('✓ 正確！','✓ Correct!'):t('✕ 再試一次','✕ Not quite')}</strong><p>{t('正確發音：','The sound was')} /{items[chooseTarget].vowel}/</p><small>{t('點選任一答案即可再次聆聽比較。','Tap either answer to compare the sounds.')}</small><button className="next-question-button challenge-action-button" onClick={()=>{
if (!isFull && freeUsage.questions >= 3) {
  setPaywallReason('questions')
  setChoosePhase('compare')
  return
}

if (chooseQuestion >= 5) {
  setChoosePhase('result')
} else {
  setChooseQuestion(q=>q+1)
  setChooseAnswer(null)
  setChooseHasListened(false)
  setChooseTarget(Math.random()<.5?0:1)
  setChooseChallengePairIndex(
    freeStage===2 ? Math.floor(Math.random()*2) : Math.min(freeStage,1)
  )
}
              }}>{chooseQuestion>=5?t('查看結果 →',t('查看結果 →','See result →')):t('下一題 →','Next question →')}</button></div>}
            </div>
          })()}

          {lib && choosePhase==='result'&&<div className="level-result"><p className="eyebrow">{t('練習完成','Practice complete')}</p><h1>{chooseScore} / 6</h1><div className="result-actions"><button className="challenge-action-button" onClick={()=>setChoosePhase('compare')}>{t('← 再比較一次','← Compare again')}</button><button className="challenge-action-button" onClick={()=>{setChooseQuestion(0);setChooseScore(0);setChooseAnswer(null);setChooseHasListened(false);setChooseTarget(Math.random()<.5?0:1);setChoosePhase('challenge')}}>{t('重新挑戰','Retry Challenge')}</button></div></div>}
        </section>
        <SiteFooter />
    </main>
    )
  }


  const LEVEL2_VOWELS = [
    { vowel: 'æ', word: 'bat', ipa: '/bæt/' },
    { vowel: 'ɛ', word: 'bet', ipa: '/bɛt/' },
    { vowel: 'ɪ', word: 'bit', ipa: '/bɪt/' },
  ] as const

  function stopL2Loop() {
    l2LoopRef.current = false
    setL2Looping(false)
    setL2PlayingWord(null)
    window.speechSynthesis.cancel()
  }

  function playL2Word(word: string, onDone?: () => void) {
    window.speechSynthesis.cancel()
    setL2PlayingWord(word)
    const utterance = new SpeechSynthesisUtterance(word)
    utterance.lang = 'en-US'
    utterance.rate = 0.9
    utterance.pitch = 1
    utterance.onend = () => {
      setL2PlayingWord(null)
      onDone?.()
    }
    utterance.onerror = () => setL2PlayingWord(null)
    window.speechSynthesis.speak(utterance)
  }

  function toggleL2Loop() {
    if (l2LoopRef.current) {
      stopL2Loop()
      return
    }
    if (!tryUseRepeat()) return
    stopQaLoop()
    stopContrastRepeat()
    stopASoundRepeat()
    window.speechSynthesis.cancel()
    l2LoopRef.current = true
    setL2Looping(true)

    const playAt = (index: number) => {
      if (!l2LoopRef.current) return
      const item = LEVEL2_VOWELS[index]
      setL2PlayingWord(item.word)
      const utterance = new SpeechSynthesisUtterance(item.word)
      utterance.lang = 'en-US'
      utterance.rate = 0.9
      utterance.pitch = 1
      utterance.onend = () => {
        if (!l2LoopRef.current) return
        window.setTimeout(() => playAt((index + 1) % LEVEL2_VOWELS.length), 450)
      }
      utterance.onerror = stopL2Loop
      window.speechSynthesis.speak(utterance)
    }
    playAt(0)
  }

  function startL2Challenge() {
    stopL2Loop()
    setL2Question(0)
    setL2Score(0)
    setL2AnswerIndex(null)
    setL2HasListened(false)
    setL2TargetIndex(Math.floor(Math.random() * LEVEL2_VOWELS.length))
    setL2Phase('challenge')
  }

  function playL2ChallengeTarget() {
    const target = LEVEL2_VOWELS[l2TargetIndex]
    setL2HasListened(true)
    playL2Word(target.word)
  }

  function answerL2(index: number) {
    if (!l2HasListened || l2AnswerIndex !== null) return
    setL2AnswerIndex(index)
    if (index === l2TargetIndex) setL2Score((score) => score + 1)
  }

  function nextL2Question() {
    if (l2Question >= 5) {
      setL2Phase('result')
      return
    }
    setL2Question((q) => q + 1)
    setL2AnswerIndex(null)
    setL2HasListened(false)
    setL2TargetIndex(Math.floor(Math.random() * LEVEL2_VOWELS.length))
  }

  const TEST_BANK: readonly TestBankItem[] = [
    // /b_t/ frame — five vowels
    { vowel:'æ', word:'bat', ipa:'/bæt/', frame:'/b_t/', diagnosticContrast:'ɛ' },
    { vowel:'ɛ', word:'bet', ipa:'/bɛt/', frame:'/b_t/', diagnosticContrast:'ɪ' },
    { vowel:'ɪ', word:'bit', ipa:'/bɪt/', frame:'/b_t/', diagnosticContrast:'ɛ' },
    { vowel:'ʌ', word:'but', ipa:'/bʌt/', frame:'/b_t/', diagnosticContrast:'ɑ' },
    { vowel:'ɑ', word:'bot', ipa:'/bɑt/', frame:'/b_t/', diagnosticContrast:'ʌ' },

    // Additional controlled contrasts for the three vowels that do not fit cleanly
    // into the same /b_t/ lexical frame.
    { vowel:'ʊ', word:'full', ipa:'/fʊl/', frame:'/f_l/', contrast:['ʊ','uː'] },
    { vowel:'uː', word:'fool', ipa:'/fuːl/', frame:'/f_l/', contrast:['ʊ','uː'] },
    { vowel:'ɪ', word:'ship', ipa:'/ʃɪp/', frame:'/ʃ_p/', contrast:['ɪ','iː'] },
    { vowel:'iː', word:'sheep', ipa:'/ʃiːp/', frame:'/ʃ_p/', contrast:['ɪ','iː'] },

    // Second controlled frames improve random coverage and reduce memorizing one word set.
    { vowel:'ʊ', word:'pull', ipa:'/pʊl/', frame:'/p_l/', contrast:['ʊ','uː'] },
    { vowel:'uː', word:'pool', ipa:'/puːl/', frame:'/p_l/', contrast:['ʊ','uː'] },
    { vowel:'ɪ', word:'bit', ipa:'/bɪt/', frame:'/b_t/', contrast:['ɪ','iː'] },
    { vowel:'iː', word:'beat', ipa:'/biːt/', frame:'/b_t/', contrast:['ɪ','iː'] },

    // Additional core vowels use short, recognizable contrasts where possible.
    { vowel:'ɔ', word:'all', ipa:'/ɔl/', frame:'/_l/', contrast:['ɔ','aʊ'] },
    { vowel:'aʊ', word:'owl', ipa:'/aʊl/', frame:'/_l/', contrast:['ɔ','aʊ'] },
    { vowel:'ə', word:'about', ipa:'/əˈbaʊt/', frame:'schwa-initial', contrast:['ə','ʌ'] },
    { vowel:'eɪ', word:'late', ipa:'/leɪt/', frame:'/l_t/', contrast:['eɪ','aɪ'] },
    { vowel:'aɪ', word:'light', ipa:'/laɪt/', frame:'/l_t/', contrast:['eɪ','aɪ'] },
    { vowel:'ɔɪ', word:'boy', ipa:'/bɔɪ/', frame:'/b_/', contrast:['ɔɪ','aɪ'] },
    { vowel:'aɪ', word:'buy', ipa:'/baɪ/', frame:'/b_/', contrast:['ɔɪ','aɪ'] },
    { vowel:'oʊ', word:'no', ipa:'/noʊ/', frame:'/n_/', contrast:['oʊ','aʊ'] },
    { vowel:'aʊ', word:'now', ipa:'/naʊ/', frame:'/n_/', contrast:['oʊ','aʊ'] },
  ] as const

  function startDiagnostic(){
    if (!isFull && freeUsage.questions >= 3) {
      setPaywallReason('questions')
      return
    }

    posthog.capture('practice_started', { mode: 'vowel_test' })
    const nextTarget = Math.floor(Math.random()*TEST_BANK.length)
    stopL2Loop();setDiagQuestion(0);setDiagAnswer(null);setDiagListened(false);setDiagStarted(true);setDiagMistakes({});setDiagScore(0);setDiagTarget(nextTarget);setDiagChoices(vowelTestChoices(TEST_BANK[nextTarget], TEST_BANK))
  }

  function startConsonantTest() {
    if (!isFull && freeUsage.questions >= 3) {
      setPaywallReason('questions')
      return
    }
    stopConsonantLoop()
    setPaywallReason(null)
    setConsonantTestQuestions(consonantTestRound())
    setConsonantTestIndex(0)
    setConsonantTestAnswer(null)
    setConsonantTestListened(false)
    setConsonantTestScore(0)
    setConsonantTestMisses([])
    setScreen('consonantTest')
  }

  if (screen === 'level2proto') {
    const done=diagQuestion>=10
    const targetItem=TEST_BANK[diagTarget]
    const diagAnswerCorrect=diagAnswer!==null&&TEST_BANK[diagAnswer]?.vowel===targetItem.vowel
    const recommendations=weakContrastRecommendations(diagMistakes)
    return <main className="app-shell"><section className="app-card vowel-test-card">
      <header className="header"><div><div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div><p className="tagline">{t('找出你最需要加強的母音組合。', 'Find which vowel contrasts need more practice.')}</p></div></header>
      <MainNav active="level2" />
          <ProductInfo />

      {!diagStarted&&!done&&<div className="diagnostic-intro"><p className="eyebrow">{t('母音測驗 · 診斷','Vowel Test · Diagnostic')}</p><h1 className="contrast-title">{t('10 題隨機聽力測驗', '10 random listening questions')}</h1><p className="contrast-subtitle"></p><div className="diagnostic-frame">{t('15 個母音 · 10 題聽力測驗', '15 vowel sounds · 10 questions')}</div><button className="start-challenge-button" onClick={startDiagnostic}>{t('開始 10 題測驗 →', 'Start 10-Question Test →')}</button></div>}

      {paywallReason === 'questions' && renderPaywall()}

      {diagStarted&&!done&&<>
        <div className="challenge-topbar">
          <p className="eyebrow">{t('母音測驗 · 挑戰','Vowel Test · Challenge')}</p>
          <button className="challenge-exit" onClick={()=>{
            setDiagStarted(false);setDiagAnswer(null);setDiagListened(false)
          }}>{t('離開 ×','Exit ×')}</button>
        </div>

        <h1 className="contrast-title">{t('你聽到哪一個母音？', 'Which vowel do you hear?')}</h1>
        <p className="question-count">{t('第','Question')} {diagQuestion+1} / 10 {t('題','')}</p>

        <p className={`listen-instruction ${diagListened?'done':''}`}>
          {diagListened?t('請在下列點選你聽到是哪一個音標','Now choose the vowel you heard.'):t('先聽聲音','Listen first')}
        </p>

        <button className={`challenge-sound ${diagListened?'played':''}`} onClick={()=>{
          setDiagListened(true);playL2Word(targetItem.word)
        }}>
          <span>🔊</span>
          <strong>{diagListened?t('再播放一次','Play again'):t('先聽聲音','Hear vowel')}</strong>
        </button>

        

        <div className="challenge-choices diagnostic-guided-choices">
          {diagChoices.map((item)=>{
            const answered=diagAnswer!==null
            const selectedVowel=diagAnswer===null?null:TEST_BANK[diagAnswer]?.vowel
            const correct=answered&&item.vowel===targetItem.vowel
            const wrong=answered&&item.vowel===selectedVowel&&item.vowel!==targetItem.vowel
            const stateClass=correct?'answer-correct':wrong?'answer-wrong':''
            return <button
              key={`${item.frame}-${item.word}-${item.vowel}`}
              disabled={!diagListened}
              className={`${stateClass} ${!diagListened?'locked':''} ${answered?'answer-listenable':''}`}
              onClick={()=>{
                if(answered){playL2Word(item.word);return}
                const selectedIndex=TEST_BANK.findIndex((candidate)=>candidate.vowel===item.vowel&&candidate.word===item.word)
                if (!isFull) {
                  setFreeUsage((current) => ({
                    ...current,
                    questions: current.questions + 1,
                  }))
                }
                setDiagAnswer(selectedIndex)
                if(item.vowel===targetItem.vowel)setDiagScore(s=>s+1)
                else {
                  const practiceVowel = targetItem.contrast?.find((vowel) => vowel !== targetItem.vowel) ?? targetItem.diagnosticContrast
                  if (practiceVowel) setDiagMistakes(prev=>{
                    const old=prev[targetItem.vowel]||{count:0,chosen:{}}
                    return {...prev,[targetItem.vowel]:{count:old.count+1,chosen:{...old.chosen,[practiceVowel]:(old.chosen[practiceVowel]||0)+1}}}
                  })
                }
              }}
            >
              <span className="answer-vowel">/{item.vowel}/{answered&&<span className="answer-speaker"> 🔊</span>}</span>
              {!diagListened&&<small>🔒 {t('先聽聲音','Listen first')}</small>}
              {correct&&<small>✓ {t('正確','Correct sound')}</small>}
              {wrong&&<small>✕ {t('你的選擇','Your choice')}</small>}
              {answered&&!correct&&!wrong&&<small>{t('可點選聆聽','Tap to listen')}</small>}
            </button>
          })}
        </div>

        {diagAnswer!==null&&<div className={`challenge-feedback ${diagAnswerCorrect?'correct':'wrong'}`}>
          <div className="feedback-symbol">{diagAnswerCorrect?'✓':'✕'}</div>
          <strong className="feedback-title">{diagAnswerCorrect?t('正確！',t('正確！','Correct!')):t('再試一次',t('再試一次','Not quite'))}</strong>
          {!diagAnswerCorrect&&<p className="feedback-detail">
            {t('你選擇了','You chose')} /{TEST_BANK[diagAnswer].vowel}/ · {t('正確發音是','The sound was')} <b>/{targetItem.vowel}/</b>
          </p>}
          <div className="feedback-word">{targetItem.word} <span>{targetItem.ipa}</span></div>
          <div className="challenge-feedback-actions">
            <button onClick={()=>playL2Word(targetItem.word)}>{t('🔊 再播放一次','🔊 Hear again')}</button>
            <button className="next-primary" onClick={()=>{
              if (diagQuestion !== 9 && !isFull && freeUsage.questions >= 3) {
                setPaywallReason('questions')
                setDiagStarted(false)
                setDiagAnswer(null)
                setDiagListened(false)
                return
              }

              if (diagQuestion === 9) {
                posthog.capture('vowel_test_completed', {
                  total_questions: 10,
                  correct_count: diagScore,
                  missed_count: 10 - diagScore,
                })
              }
              const nextTarget = Math.floor(Math.random()*TEST_BANK.length)
              setDiagQuestion(q=>q+1);setDiagAnswer(null);setDiagListened(false);setDiagTarget(nextTarget);setDiagChoices(vowelTestChoices(TEST_BANK[nextTarget], TEST_BANK))
            }}>{diagQuestion===9?t('查看結果 →',t('查看結果 →','See result →')):t('下一題 →',t('下一題 →','Next →'))}</button>
          </div>
        </div>}
      </>}
      {done&&<div className="diagnostic-results"><p className="eyebrow">{t('測驗完成','Diagnostic complete')}</p><h1>{diagScore} / 10</h1>{recommendations.length===0?<p>{t('這一輪沒有偵測到需要特別加強的母音。','No weak vowel was detected in this round.')}</p>:<><h2>{t('需要加強', 'Needs Practice')}</h2><div className="mistake-list">{recommendations.map(({target,chosen,count})=><div key={`${target}-${chosen}`}><strong>/{target}/ ↔ /{chosen}/</strong><span>{t('答錯','Missed')} {count}×</span></div>)}</div>
        {recommendations.some(x=>CHOOSE2_LIBRARY.some(lib=>lib.vowels.includes(x.target as never)&&lib.vowels.includes(x.chosen as never)))&&<button className="start-challenge-button" onClick={()=>{
          const first=recommendations.find(x=>CHOOSE2_LIBRARY.some(lib=>lib.vowels.includes(x.target as never)&&lib.vowels.includes(x.chosen as never)))
          if(first){setFreeChosen([first.target,first.chosen]);setFreeStage(0);setChoosePhase('compare');setScreen('choose2')}
          else setScreen('choose2')
        }}>{t('練習較弱的母音 →','Practice weak sounds →')}</button>}</>}<button className="secondary-test-button" onClick={startDiagnostic}>{t('再測 10 題 →','Try another 10 →')}</button></div>}
    </section><SiteFooter /></main>
  }


  if (screen === 'contrast') {
    const target = contrastPair[challengeTarget]
    const correct = challengeAnswer === challengeTarget

    return (
      <main className="app-shell">
        <section className="app-card contrast-practice">
          <header className="header">
            <div>
              <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div>
              <p className="tagline">{t('專注聆聽音標的差異。','Hear the vowel, not the consonants.')}</p>
            </div>
          </header>
          <MainNav active="guided" />
          <InAppBrowserNotice />
          <ProductInfo />

          {contrastPhase === 'learn' && (
            <>
              <p className="eyebrow">{t('第 1 級 · /æ/ vs /ɛ/','Level 1 · /æ/ vs /ɛ/')}</p>
              <h1 className="contrast-title">{contrastStage.id} — {contrastStage.title}</h1>
              <p className="contrast-subtitle">{t('請點選組別練習：','Choose a group to practice:')}</p>

              <div className="level-stage-tabs">
                {LEVEL1_STAGES.map((stage, index) => (
                  <button
                    key={stage.id}
                    className={contrastStageIndex === index ? 'active' : ''}
                    onClick={() => {
                      stopContrastRepeat()
                      setContrastStageIndex(index)
                      setContrastPairIndex(0)
                    }}
                  >
                    <strong>{stage.id}</strong>
                    <span>{stage.id === '1C' ? t('混合練習','Mixed Frames') : stage.title}</span>
                  </button>
                ))}
              </div>

              {contrastStage.pairs.length > 1 && (
                <div className="pair-tabs">
                  {contrastStage.pairs.map((pair, index) => (
                    <button
                      key={`${pair.left.word}-${pair.right.word}`}
                      className={contrastPairIndex === index ? 'active' : ''}
                      onClick={() => {
                        stopContrastRepeat()
                        setContrastPairIndex(index)
                      }}
                    >
                      {pair.left.word} / {pair.right.word}
                    </button>
                  ))}
                </div>
              )}

              <div className="contrast-cards">
                {(['left', 'right'] as const).map((side) => {
                  const item = contrastPair[side]
                  const repeating = contrastRepeatSide === side
                  return (
                    <div key={side} className="contrast-card contrast-learn-card">
                      <span className="contrast-vowel">/{item.vowel}/</span>
                      <strong>{item.word}</strong>
                      <span className="contrast-ipa">{item.ipa}</span>
                      <VowelWordMeaning word={item.word} />
                      <div className="learn-audio-actions">
                        <button onClick={() => {
                          stopContrastRepeat()
                          speakWord(item.word, 0.82)
                        }}>🔊 {t('播放','Listen')}</button>
                        <button
                          className={repeating ? 'repeating' : ''}
                          onClick={() => startContrastRepeat(side)}
                        >
                          {repeating ? t('■ 停止','■ Stop') : t('∞ 重複','∞ Repeat')}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>

              <div className="loop-control-panel compact">
                <div className="loop-word-indicators">
                  {(contrastStage.id === '1C'
                    ? contrastStage.pairs.flatMap((pair) => [pair.left, pair.right])
                    : [contrastPair.left, contrastPair.right]
                  ).map((item) => (
                    <span
                      key={item.word}
                      className={loopPlayingWord === item.word ? 'playing' : ''}
                    >
                      {item.word.toUpperCase()}
                      {loopPlayingWord === item.word && <b> 🔊</b>}
                    </span>
                  ))}
                </div>
                <button
                  className={`ab-loop-button ${isABLooping ? 'active' : ''}`}
                  onClick={toggleABLoop}
                >
                  {isABLooping
                    ? t('■ 停止循環','■ Stop Loop')
                    : contrastStage.id === '1C'
                      ? t('∞ 4 音循環','∞ 4-Sound Loop')
                      : t('∞ A/B 循環','∞ A/B Loop')}
                </button>
              </div>

              <div className="contrast-hint">
                <span>{contrastPair.left.word[0]}</span>
                <strong> /{contrastPair.left.vowel}/ ↔ /{contrastPair.right.vowel}/ </strong>
                <span>{contrastPair.left.word.slice(-1)}</span>
              </div>

<button className="start-challenge" onClick={startChallenge}>
  {t(`開始 ${contrastStage.id} 的挑戰 →`,`Start ${contrastStage.id} Challenge →`)}
</button>

{(paywallReason === 'questions' || paywallReason === 'repeat') && renderPaywall()}
            </>
          )}

          {contrastPhase === 'challenge' && (
            <>
              <div className="challenge-topbar">
                <p className="eyebrow">{t('第 1 級 · 挑戰','Level 1 · Challenge')}</p>
                <button className="challenge-exit" onClick={() => {
                  stopContrastRepeat()
                  setChallengeAnswer(null)
                  setHasListened(false)
                  setContrastPhase('learn')
                }}>{t('離開 ×','Exit ×')}</button>
              </div>
              <h1 className="contrast-title">{t('你聽到哪一個母音？', 'Which vowel do you hear?')}</h1>
              <p className="question-count">{t('第','Question')} {challengeQuestion + 1} / 6 {t('題','')}</p>

              <p className={`listen-instruction ${hasListened ? 'done' : ''}`}>
                {hasListened ? t('請在下列點選你聽到是哪一個音標','Now choose the vowel you heard.') : t('先聽聲音','Listen first')}
              </p>
              <button className={`challenge-sound ${hasListened ? 'played' : ''}`} onClick={() => {
                setHasListened(true)
                speakWord(target.word, 0.82)
              }}>
                <span>🔊</span><strong>{hasListened ? t('再播放一次','Play again') : t('先聽聲音','Hear vowel')}</strong>
              </button>

              <div className="challenge-choices">
                {(['left', 'right'] as const).map((side) => {
                  const isChosen = challengeAnswer === side
                  const isCorrectAnswer = challengeAnswer !== null && side === challengeTarget
                  const isWrongChoice = isChosen && side !== challengeTarget
                  const stateClass = isCorrectAnswer ? 'answer-correct' : isWrongChoice ? 'answer-wrong' : isChosen ? 'chosen' : ''

                  return (
                    <button
                      key={side}
                      disabled={!hasListened}
                      className={`${stateClass} ${!hasListened ? 'locked' : ''} ${challengeAnswer !== null ? 'answer-listenable' : ''}`}
                      onClick={() => {
                        if (challengeAnswer === null) {
                          answerChallenge(side)
                        } else {
                          speakWord(contrastPair[side].word, 0.82)
                        }
                      }}
                    >
                      <span className="answer-vowel">
                        /{contrastPair[side].vowel}/
                        {challengeAnswer !== null && <span className="answer-speaker">🔊</span>}
                      </span>
                      {!hasListened && <small>🔒 {t('先聽聲音','Listen first')}</small>}
                      {isCorrectAnswer && <small>✓ {t('正確','Correct sound')}</small>}
                      {isWrongChoice && <small>✕ {t('你的選擇','Your choice')}</small>}
                      {challengeAnswer !== null && !isCorrectAnswer && !isWrongChoice && <small>{t('可點選聆聽','Tap to listen')}</small>}
                    </button>
                  )
                })}
              </div>

              {challengeAnswer && (
                <div className={`challenge-feedback ${correct ? 'correct' : 'wrong'}`}>
                  <div className="feedback-symbol">{correct ? '✓' : '✕'}</div>
                  <strong className="feedback-title">{correct ? t('正確！','Correct!') : t('再試一次','Not quite')}</strong>
                  {!correct && (
                    <p className="feedback-detail">
                      {t('你選擇了','You chose')} /{contrastPair[challengeAnswer].vowel}/ · {t('正確發音是','The sound was')} <b>/{target.vowel}/</b>
                    </p>
                  )}
                  <div className="feedback-word">{target.word} <span>{target.ipa}</span></div>

                  <div className="challenge-feedback-actions">
                    <button onClick={() => speakWord(target.word, 0.82)}>{t('🔊 再播放一次','🔊 Hear again')}</button>
                    <button className="next-primary" onClick={nextChallengeQuestion}>
                      {challengeQuestion >= 5 ? t('查看結果 →','See result →') : t('下一題 →','Next →')}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}

          {contrastPhase === 'result' && (
            <div className="level-result">
              <p className="eyebrow">{contrastStage.id} {t('完成','complete')} · {contrastStage.title}</p>
              <h1>{challengeScore} / 6</h1>
              <p>{challengeScore >= 5 ? t('太棒了！你已經能分辨 /æ/ 和 /ɛ/ 的差異。', 'Excellent — your ear is separating /æ/ and /ɛ/.') :
                challengeScore >= 4
                  ? t('不錯，再練一輪可以讓兩個音的差異更清楚。', 'Clear — one more round can make the contrast stronger.')
                  : t('繼續比較這兩個音，再挑戰一次。', 'Keep comparing the pairs, then try again.')}</p>
              <div className="result-actions">
                <button className="challenge-action-button" onClick={() => setContrastPhase('learn')}>{t('← 再比較一次','← Compare again')}</button>
                {contrastStageIndex < LEVEL1_STAGES.length - 1 ? (
                  <button onClick={() => {
                    setContrastStageIndex((index) => index + 1)
                    setContrastPairIndex(0)
                    setContrastPhase('learn')
                  }}>{t('下一組：','Next: ')}{LEVEL1_STAGES[contrastStageIndex + 1].id} →</button>
                ) : (
                  <button className="challenge-action-button" onClick={startChallenge}>{t('重新挑戰','Retry Challenge')}</button>
                )}
              </div>
            </div>
          )}
        </section>
        <SiteFooter />
    </main>
    )
  }

  if (screen === 'consonantTest') {
    const question = consonantTestQuestions[consonantTestIndex]
    const pair = question && CONSONANT_PAIRS[question.pairIndex]
    const target = pair?.sounds[question.targetIndex]
    const done = consonantTestIndex >= 10
    return <main className="app-shell"><section className="app-card vowel-test-card consonant-test-card">
      <header className="header"><div><div className="brand-row">
        <div className="brand">EnSound <span>UP</span></div>
        <div className="language-switch" aria-label="Language">
          <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
          <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
        </div>
      </div><p className="tagline">{t('聆聽並辨認子音。', 'Listen and identify consonants.')}</p></div></header>
      <MainNav active="consonantTest" />
      {consonantTestQuestions.length === 0 && <div className="diagnostic-intro">
        <p className="eyebrow">{t('子音綜合測驗', 'Consonant Comprehensive Test')}</p>
        <h1 className="contrast-title">{t('10 題混合聽力測驗', '10 mixed listening questions')}</h1>
        <p className="diagnostic-frame">{t('18 組子音對比 · 每題 4 個選項', '18 contrasts · 4 choices each')}</p>
        <button className="start-challenge-button" onClick={startConsonantTest}>{t('開始 10 題測驗 →', 'Start 10-Question Test →')}</button>
        {paywallReason === 'questions' && renderPaywall()}
      </div>}
      {question && !done && target && pair && <>
        <div className="challenge-topbar">
          <p className="eyebrow">{t('子音綜合測驗', 'Consonant Comprehensive Test')}</p>
          <button className="challenge-exit" onClick={() => { setConsonantTestQuestions([]); setConsonantPhase('compare'); setScreen('consonant') }}>{t('離開 ×', 'Exit ×')}</button>
        </div>
        <h1 className="contrast-title">{t('你聽到哪個子音？', 'Which consonant do you hear?')}</h1>
        <p className="question-count">{t('第', 'Question')} {consonantTestIndex + 1} / 10 {t('題', '')}</p>
        <p className={`listen-instruction ${consonantTestListened ? 'done' : ''}`}>
          {consonantTestListened ? t('現在選擇你聽到的子音。', 'Now choose the consonant you heard.') : t('先聽聲音', 'Listen first')}
        </p>
        <button className={`challenge-sound ${consonantTestListened ? 'played' : ''}`} onClick={() => {
          setConsonantTestListened(true); speakWord(target.word, 0.82)
        }}><span>🔊</span><strong>{consonantTestListened ? t('再播放一次', 'Play again') : t('先聽聲音', 'Listen first')}</strong></button>
        <div className="challenge-choices diagnostic-guided-choices">
          {question.choices.map((sound) => {
            const answered = consonantTestAnswer !== null
            const correct = answered && sound === target.sound
            const wrong = answered && sound === consonantTestAnswer && !correct
            return <button key={sound} disabled={!consonantTestListened}
              className={`${correct ? 'answer-correct' : wrong ? 'answer-wrong' : ''} ${!consonantTestListened ? 'locked' : ''}`}
              onClick={() => {
                if (answered) return
                setConsonantTestAnswer(sound)
                if (!isFull) setFreeUsage((current) => ({ ...current, questions: current.questions + 1 }))
                if (sound === target.sound) setConsonantTestScore((score) => score + 1)
                else setConsonantTestMisses((misses) => [...misses, question.pairIndex])
              }}>
              <span className="answer-vowel">/{sound}/</span>
              {!consonantTestListened && <small>🔒 {t('先聽聲音', 'Listen first')}</small>}
              {correct && <small>✓ {t('正確', 'Correct sound')}</small>}
              {wrong && <small>✕ {t('你的選擇', 'Your choice')}</small>}
            </button>
          })}
        </div>
        {consonantTestAnswer !== null && <div className={`challenge-feedback ${consonantTestAnswer === target.sound ? 'correct' : 'wrong'}`}>
          <div className="feedback-symbol">{consonantTestAnswer === target.sound ? '✓' : '✕'}</div>
          <strong className="feedback-title">{consonantTestAnswer === target.sound ? t('正確！', 'Correct!') : t('再試一次', 'Not quite')}</strong>
          {consonantTestAnswer !== target.sound && <p className="feedback-detail">{t('你選擇了', 'You chose')} /{consonantTestAnswer}/ · {t('正確發音是', 'The sound was')} /{target.sound}/</p>}
          <div className="feedback-word">{target.word.toUpperCase()} <span>{target.ipa}</span></div>
          <div className="challenge-feedback-actions">
            <button onClick={() => speakWord(target.word, 0.82)}>{t('🔊 再播放一次', '🔊 Hear again')}</button>
            <button className="next-primary" onClick={() => {
              if (consonantTestIndex !== 9 && !isFull && freeUsage.questions >= 3) {
                setPaywallReason('questions'); setConsonantTestQuestions([]); return
              }
              setConsonantTestIndex((index) => index + 1)
              setConsonantTestAnswer(null)
              setConsonantTestListened(false)
            }}>{consonantTestIndex === 9 ? t('查看結果 →', 'See result →') : t('下一題 →', 'Next →')}</button>
          </div>
        </div>}
      </>}
      {done && <div className="diagnostic-results">
        <p className="eyebrow">{t('測驗完成', 'Test complete')}</p>
        <h1>{consonantTestScore} / 10</h1>
        {consonantTestMisses.length > 0 ? <><h2>{t('需要加強的對比', 'Contrasts to review')}</h2>
          <div className="mistake-list">{[...new Set(consonantTestMisses)].map((pairIndex) => {
            const weakPair = CONSONANT_PAIRS[pairIndex]
            return <div key={weakPair.id}>
              <strong>/{weakPair.sounds[0].sound}/ ↔ /{weakPair.sounds[1].sound}/</strong>
              <span>{t('答錯', 'Missed')} {consonantTestMisses.filter((miss) => miss === pairIndex).length}×</span>
              <button type="button" onClick={() => {
                selectConsonantPair(pairIndex)
                setConsonantTestQuestions([])
                setScreen('consonant')
              }}>{t('開始練習', 'Practice')}</button>
            </div>
          })}</div></>
          : <p>{t('這一輪沒有答錯的子音對比。', 'No missed consonant contrasts this round.')}</p>}
        <button className="start-challenge-button" onClick={() => { setConsonantTestQuestions([]); setConsonantPhase('compare'); setScreen('consonant') }}>{t('← 返回子音練習', '← Consonant Practice')}</button>
        <button className="secondary-test-button" onClick={startConsonantTest}>{t('再測 10 題 →', 'Retry Test →')}</button>
        {paywallReason === 'questions' && renderPaywall()}
      </div>}
    </section><SiteFooter /></main>
  }

  if (screen === 'consonant') {
    const target = consonantSounds[consonantTarget]
    return (
      <main className="app-shell">
        <section className="app-card contrast-practice consonant-practice">
<header className="header">
  <div>
    <DevAccessSwitch
      accessLevel={accessLevel}
      setAccessLevel={setAccessLevel}
    />
    <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button type="button" className="upgrade-full-button" onClick={() => setShowProductInfo(true)}>{t('解鎖完整版', 'Unlock Full')}</button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">{t('意見回饋', 'Feedback')}</a>
                  <a href="mailto:uptools.support@gmail.com">{t('聯絡我們', 'Contact')}</a>
                </div>
              </div>
              <p className="tagline">{t('專注聆聽子音的差異。', 'Focus on the difference between consonant sounds.')}</p>
            </div>
          </header>
          <MainNav active="consonant" />
          <InAppBrowserNotice />
          <ProductInfo />

          <p className="eyebrow">/{consonantSounds[0].sound}/ ↔ /{consonantSounds[1].sound}/</p>
          <h1 className="contrast-title">{t('子音練習', 'Consonant Practice')}</h1>
          <p className="contrast-subtitle">{t('專注聆聽子音的差異。', 'Focus on the difference between consonant sounds.')}</p>
          <div className="pair-tabs consonant-pair-tabs" role="group" aria-label={t('選擇子音組合', 'Choose a consonant pair')}>
            {CONSONANT_PAIRS.map((pair, index) => (
              <button key={pair.id} type="button" className={consonantPairIndex === index ? 'active' : ''}
                aria-pressed={consonantPairIndex === index}
                onClick={() => {
                  if (index === consonantPairIndex) return
                  selectConsonantPair(index)
                }}>
                /{pair.sounds[0].sound}/ ↔ /{pair.sounds[1].sound}/
              </button>
            ))}
          </div>

          {consonantPhase === 'compare' ? (
            <>
              <div className="contrast-cards">
                {consonantSounds.map((item) => (
                  <div className="contrast-card contrast-learn-card" key={item.sound}>
                    <span className="contrast-vowel">/{item.sound}/</span>
                    <strong>{item.word.toUpperCase()}</strong>
                    <span className="contrast-ipa">{item.ipa}</span>
                    {isZh && <span style={{ color: '#7d86aa', fontSize: 15, lineHeight: 1.4 }}>{item.partOfSpeech} {item.meaningZh}</span>}
                    <div className="learn-audio-actions">
                      <button onClick={() => { stopConsonantLoop(); speakWord(item.word, 0.82) }}>🔊 {t('播放', 'Play')}</button>
                    </div>
                  </div>
                ))}
              </div>
              <p className="consonant-note">{consonantSounds[0].word.toUpperCase()} ↔ {consonantSounds[1].word.toUpperCase()} · {t(consonantPair.noteZh, consonantPair.noteEn)}</p>
              <div className="loop-control-panel compact">
                <div className="loop-word-indicators">
                  {consonantSounds.map((item) => (
                    <span key={item.sound} className={consonantPlayingWord === item.word ? 'playing' : ''}>{item.word.toUpperCase()}</span>
                  ))}
                </div>
                <button className={`ab-loop-button ${consonantLooping ? 'active' : ''}`} onClick={toggleConsonantLoop}>
                  {consonantLooping ? t('■ 停止循環', '■ Stop Loop') : t('∞ A/B 循環', '∞ A/B Loop')}
                </button>
              </div>
              <button className="start-challenge" onClick={() => {
                stopConsonantLoop()
                setConsonantQuestion(0)
                setConsonantScore(0)
                setConsonantTarget(Math.random() < 0.5 ? 0 : 1)
                setConsonantListened(false)
                setConsonantAnswer(null)
                setConsonantPhase('challenge')
              }}>{t('開始挑戰 →', 'Start Challenge →')}</button>
              <button className="secondary-test-button" onClick={() => { setConsonantTestQuestions([]); setPaywallReason(null); setScreen('consonantTest') }}>{t('子音綜合測驗 · 10 題 →', 'Consonant Comprehensive Test · 10 →')}</button>
              {paywallReason === 'repeat' && renderPaywall()}
            </>
          ) : consonantPhase === 'challenge' ? (
            <>
              <div className="challenge-topbar">
                <p className="eyebrow">{t('子音練習 · 挑戰', 'Consonant Practice · Challenge')}</p>
                <button className="challenge-exit" onClick={() => {
                  setConsonantAnswer(null)
                  setConsonantListened(false)
                  setConsonantPhase('compare')
                }}>{t('離開 ×', 'Exit ×')}</button>
              </div>
              <h2 className="consonant-question">{t('你聽到哪個子音？', 'Which consonant do you hear?')}</h2>
              <p className="question-count">{t('第', 'Question')} {consonantQuestion + 1} / 6 {t('題', '')}</p>
              <p className={`listen-instruction ${consonantListened ? 'done' : ''}`}>
                {consonantListened ? t('現在選擇你聽到的子音。', 'Now choose the consonant you heard.') : t('先聽聲音', 'Listen first')}
              </p>
              <button className={`challenge-sound ${consonantListened ? 'played' : ''}`} onClick={() => {
                setConsonantListened(true)
                speakWord(target.word, 0.82)
              }}><span>🔊</span><strong>{consonantListened ? t('再播放一次', 'Play again') : t('先聽聲音', 'Listen first')}</strong></button>
              <div className="challenge-choices">
                {consonantSounds.map((item, index) => {
                  const chosen = consonantAnswer === index
                  const correct = consonantAnswer !== null && consonantTarget === index
                  const wrong = chosen && !correct
                  return (
                    <button key={item.sound} disabled={!consonantListened}
                      className={`${correct ? 'answer-correct' : wrong ? 'answer-wrong' : ''} ${!consonantListened ? 'locked' : ''} ${consonantAnswer !== null ? 'answer-listenable' : ''}`}
                      onClick={() => {
                        if (consonantAnswer !== null) {
                          speakWord(item.word, 0.82)
                        } else {
                          setConsonantAnswer(index as 0 | 1)
                          if (index === consonantTarget) setConsonantScore((score) => score + 1)
                        }
                      }}>
                      <span className="answer-vowel">/{item.sound}/{consonantAnswer !== null && <span className="answer-speaker">🔊</span>}</span>
                      {!consonantListened && <small>🔒 {t('先聽聲音', 'Listen first')}</small>}
                      {correct && <small>✓ {t('正確', 'Correct sound')}</small>}
                      {wrong && <small>✕ {t('你的選擇', 'Your choice')}</small>}
                      {consonantAnswer !== null && !correct && !wrong && <small>{t('可點選聆聽', 'Tap to listen')}</small>}
                    </button>
                  )
                })}
              </div>
              {consonantAnswer !== null && (
                <div className={`challenge-feedback ${consonantAnswer === consonantTarget ? 'correct' : 'wrong'}`}>
                  <div className="feedback-symbol">{consonantAnswer === consonantTarget ? '✓' : '✕'}</div>
                  <strong className="feedback-title">{consonantAnswer === consonantTarget ? t('正確！', 'Correct!') : t('再試一次', 'Not quite')}</strong>
                  {consonantAnswer !== consonantTarget && <p className="feedback-detail">{t('你選擇了', 'You chose')} /{consonantSounds[consonantAnswer].sound}/ · {t('正確發音是', 'The sound was')} /{target.sound}/</p>}
                  <div className="feedback-word">{target.word.toUpperCase()} <span>{target.ipa}</span></div>
                  <div className="challenge-feedback-actions">
                    <button onClick={() => speakWord(target.word, 0.82)}>{t('🔊 再播放一次', '🔊 Hear again')}</button>
                    <button className="next-primary" onClick={() => {
                      if (consonantQuestion >= 5) {
                        setConsonantPhase('result')
                      } else {
                        setConsonantQuestion((question) => question + 1)
                        setConsonantTarget(Math.random() < 0.5 ? 0 : 1)
                        setConsonantListened(false)
                        setConsonantAnswer(null)
                      }
                    }}>{consonantQuestion >= 5 ? t('查看結果 →', 'See result →') : t('下一題 →', 'Next →')}</button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="level-result">
              <p className="eyebrow">{t('練習完成', 'Practice complete')} · /{consonantSounds[0].sound}/ ↔ /{consonantSounds[1].sound}/</p>
              <h1>{consonantScore} / 6</h1>
              <p>{consonantScore >= 5
                ? t(`很棒！你已經能分辨 /${consonantSounds[0].sound}/ 和 /${consonantSounds[1].sound}/ 的差異。`, `Great! You can distinguish /${consonantSounds[0].sound}/ and /${consonantSounds[1].sound}/.`)
                : consonantScore >= 4
                  ? t(`不錯，再多聽幾次 /${consonantSounds[0].sound}/ 和 /${consonantSounds[1].sound}/，差異會更清楚。`, `Nice work. Listen to /${consonantSounds[0].sound}/ and /${consonantSounds[1].sound}/ a few more times to make the difference clearer.`)
                  : t(`繼續比較 /${consonantSounds[0].sound}/ 和 /${consonantSounds[1].sound}/，再挑戰一次。`, `Keep comparing /${consonantSounds[0].sound}/ and /${consonantSounds[1].sound}/, then try the challenge again.`)}</p>
              <div className="result-actions">
                <button className="challenge-action-button" onClick={() => setConsonantPhase('compare')}>{t('← 再比較一次', '← Compare again')}</button>
                <button className="challenge-action-button" onClick={() => {
                  setConsonantQuestion(0)
                  setConsonantScore(0)
                  setConsonantTarget(Math.random() < 0.5 ? 0 : 1)
                  setConsonantListened(false)
                  setConsonantAnswer(null)
                  setConsonantPhase('challenge')
                }}>{t('重新挑戰', 'Retry Challenge')}</button>
              </div>
            </div>
          )}
        </section>
        <SiteFooter />
      </main>
    )
  }

  if (screen === 'vowelBasics') {
    return (
      <main className="app-shell">
        <section className="app-card vowel-basics">
          <header className="header">
            <div>
              <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button type="button" className="upgrade-full-button" onClick={() => setShowProductInfo(true)}>
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">{t('意見回饋','Feedback')}</a>
                  <a href="mailto:uptools.support@gmail.com">{t('聯絡我們','Contact')}</a>
                </div>
              </div>
              <p className="tagline">{t('一個字母，多種發音。', 'One letter. Many sounds.')}</p>
            </div>
          </header>
          <MainNav active="vowelBasics" />
          <InAppBrowserNotice />
          <ProductInfo />

          <p className="eyebrow">{t('母音核心說明', 'Vowel Basics')}</p>
          <h1 className="sound-map-title">{t('一個字母，不只有一種發音。', 'One letter can have more than one sound.')}</h1>
          <p className="vowel-basics-intro">
            {t('同一個字母，可以有不同的聲音。', 'The same letter can make different sounds.')}<br />
            {t('不同的字母，也可以有相同的聲音。', 'Different letters can share the same sound.')}
          </p>
          <p className="vowel-basics-guide">
            {t('看看 A、E、I、O、U 的發音地圖，你能找到哪些重複的聲音？',
              'Explore the A, E, I, O, U sound map. Which sounds can you find more than once?')}
          </p>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <h2 className="vowel-basics-heading">{t('字母發音對照', 'Letter Sound Map')}</h2>
            {isZh && <button type="button" aria-pressed={showVowelBasicsTranslations}
              onClick={() => setShowVowelBasicsTranslations((shown) => !shown)}
              style={{ marginBottom: 12, border: '1px solid #dfe3f3', borderRadius: 999, padding: '5px 10px', background: '#f7f8ff', color: '#59658f', fontSize: 12, fontWeight: 700, whiteSpace: 'nowrap', cursor: 'pointer' }}>
              翻譯：{showVowelBasicsTranslations ? '開啟' : '關閉'}
            </button>}
          </div>
          <p className="vowel-basics-hint">{t('找找看：哪些聲音重複出現？', 'Can you spot the sounds that repeat?')}</p>
          <div className="vowel-basics-map">
            {SOUND_MAP_LETTERS.map((letter) => (
              <div className="vowel-basics-row" key={letter}>
                <strong className="vowel-basics-letter">{letter}</strong>
                <div className="vowel-basics-sounds">
                  {SOUND_MAP[letter].map((sound) => (
                    <span className="vowel-basics-sound" key={sound.vowel}>
                      <span className="vowel-basics-ipa" style={REPEATED_IPA_COLORS[sound.vowel] ? { color: REPEATED_IPA_COLORS[sound.vowel] } : undefined}>
                        /{sound.vowel}/
                      </span> <span className="vowel-basics-word">{sound.word}{isZh && showVowelBasicsTranslations && <> <VowelWordMeaning word={sound.word} inline /></>}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="vowel-basics-takeaway">
            <h2>{t('你發現了嗎？', 'Did you notice?')}</h2>
            <p className="vowel-basics-discovery">
              <strong style={{ color: REPEATED_IPA_COLORS['ə'] }}>/ə/</strong> {t('可以出現在 A、E、I、O、U。', 'can appear with A, E, I, O, and U.')}
              <span className="vowel-basics-examples">{['about', 'problem', 'pencil', 'today', 'support'].map((word, index) => <span key={word}>{index > 0 && ' · '}{word} {isZh && <VowelWordMeaning word={word} inline />}</span>)}</span>
            </p>
            <p className="vowel-basics-discovery">
              <strong style={{ color: REPEATED_IPA_COLORS['ɪ'] }}>/ɪ/</strong> {t('也可以出現在 A、E、I。', 'can also appear with A, E, and I.')}
              <span className="vowel-basics-examples">{['village', 'pretty', 'sit'].map((word, index) => <span key={word}>{index > 0 && ' · '}{word} {isZh && <VowelWordMeaning word={word} inline />}</span>)}</span>
            </p>
            <p className="vowel-basics-conclusion">{t('所以真正要學的，不只是 A / E / I / O / U，而是你實際聽到的母音。',
              'The goal is to recognize the vowel you actually hear, beyond learning the letters A / E / I / O / U.')}</p>
          </div>
        </section>
        <SiteFooter />
      </main>
    )
  }

  if (screen === 'vowel') {
    return (
      <main className="app-shell">
        <section className="app-card a-sound-map">
          <header className="header">
            <div>
              <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div>
              <p className="tagline">{t('一個字母，多種發音。', 'One letter. Many sounds.')}</p>
            </div>
          </header>
          <MainNav active="vowel" />
          <InAppBrowserNotice />
          <ProductInfo />

          <p className="eyebrow">{selectedSoundLetter} Sound Map</p>
          <h1 className="sound-map-title">{t(`字母「${selectedSoundLetter}」的發音是怎樣的？`, `How can “${selectedSoundLetter}” sound?`)}</h1>
          <p className="sound-map-intro">
            {t('請點選下列音標發音練習','Tap a sound below to practice pronunciation.')}
          </p>

          <div className="sound-map-letters" role="group" aria-label={t('選擇字母', 'Choose a letter')}>
            {SOUND_MAP_LETTERS.map((letter) => (
              <button
                type="button"
                key={letter}
                className={selectedSoundLetter === letter ? 'active' : ''}
                aria-pressed={selectedSoundLetter === letter}
                onClick={() => {
                  if (selectedSoundLetter === letter) return
                  stopASoundRepeat()
                  setSelectedSoundLetter(letter)
                  setSelectedSoundIndex(0)
                }}
              >
                {letter}
              </button>
            ))}
          </div>

          <div className="sound-map-grid">
            {currentSounds.map((sound, index) => (
              <button
                type="button"
                key={sound.vowel}
                className={`sound-map-card ${selectedSoundIndex === index ? 'selected' : ''}`}
                onClick={() => {
                  stopASoundRepeat()
                  setSelectedSoundIndex(index)
                  playSoundMapWord(index)
                }}
              >
                <span className="sound-map-symbol">/{sound.vowel}/</span>
                <strong>{highlightSoundMapLetter(sound.word)}</strong>
                <small>{sound.ipa}</small>
                <VowelWordMeaning word={sound.word} />
                <span className="sound-map-speaker">🔊</span>
              </button>
            ))}
          </div>

          <section className="sound-focus-card">
            
            <div className="sound-focus-symbol">/{selectedSound.vowel}/</div>
            <div className="sound-focus-word">{highlightSoundMapLetter(selectedSound.word)}</div>
            <div className="sound-focus-ipa">{selectedSound.ipa}</div>
            <VowelWordMeaning word={selectedSound.word} />
            <p className="sound-focus-examples">{selectedSound.note.split(' · ').map((word, index) => <span key={word}>{index > 0 && ' · '}{word} {isZh && word !== selectedSound.word && <VowelWordMeaning word={word} inline />}</span>)}</p>

            <div className="sound-focus-actions">
              <button onClick={() => playSoundMapWord(selectedSoundIndex, 1)}>{t('🔊 播放','🔊 Play')}</button>
              <button onClick={() => playSoundMapWord(selectedSoundIndex, 0.62)}>{t('🐢 慢速','🐢 Slow')}</button>
              <button
                className={isASoundRepeating ? 'repeat-active' : ''}
                onClick={toggleASoundRepeat}
              >
                {isASoundRepeating ? '■' : '∞'} {isASoundRepeating ? t('停止','Stop') : t('重複','Repeat')}
              </button>
            </div>
          </section>

          {paywallReason === 'repeat' && renderPaywall()}

        </section>
        <SiteFooter />
    </main>
    )
  }

  return (
    <main className="app-shell">
      <section className="app-card">
        <header className="header">
          <div>
            <div className="brand-row">
                <div className="brand">EnSound <span>UP</span></div>
                <div className="language-switch" aria-label="Language">
                  <button className={uiLang === 'zh-TW' ? 'active' : ''} onClick={() => changeUiLang('zh-TW')}>繁中</button>
                  <button className={uiLang === 'en' ? 'active' : ''} onClick={() => changeUiLang('en')}>EN</button>
                </div>
                <div className="header-utility-links">
                  <button
                    type="button"
                    className="upgrade-full-button"
                    onClick={() => setShowProductInfo(true)}
                  >
                    {t('解鎖完整版', 'Unlock Full')}
                  </button>
                  <a href="https://forms.gle/saW24XFSynYDiFW59" target="_blank" rel="noreferrer">
                    {t('意見回饋','Feedback')}
                  </a>
                  <a href="mailto:uptools.support@gmail.com">
                    {t('聯絡我們','Contact')}
                  </a>
                </div>
              </div>
            <p className="tagline">{t('點選單字，聽清楚發音。','Tap a word. Hear it clearly.')}</p>
          </div>
          <button className="icon-button" aria-label="Settings">&#9881;</button>
        </header>

        <section className="input-section">
          <MainNav active="sentence" />
          <InAppBrowserNotice />
          <ProductInfo />

          <label htmlFor="sentence" className="section-label">{t('請輸入您想要練習的單字或者是句子：','Type a word or sentence you want to practice:')}</label>
          <textarea
            id="sentence"
            value={sentence}
            onChange={(event) => handleSentenceChange(event.target.value)}
            placeholder="Type an English sentence..."
            rows={3}
          />
          <div className="sentence-play-controls">
            <button type="button" onClick={() => speakWord(sentence, 0.95)}>
              🔊 <strong>{t('一般速度','Normal')}</strong>
            </button>
            <button type="button" onClick={() => speakWord(sentence, 0.68)}>
              🐢 <strong>{t('慢速', 'Slow')}</strong>
            </button>
          </div>
        </section>

        <section className="word-section">
          <div className="section-title-row">
            <h2>{t('請點選下列任何一個單字','Choose one word')}</h2>
            
          </div>

          <div className="word-grid">
            {words.length > 0 ? (
              words.map((word) => (
                <button
                  key={`${word.index}-${word.raw}`}
                  className={`word-chip ${selectedIndex === word.index ? 'selected' : ''}`}
                  onClick={() => handleSelect(word.index)}
                >
                  {word.raw}
                </button>
              ))
            ) : (
              <p className="empty">Type a sentence to begin.</p>
            )}
          </div>
        </section>

        <section className={`pronunciation-card ${selectedWord ? '' : 'disabled'}`}>
          <p className="eyebrow">{t('發音練習','Pronunciation target')}</p>
          <h1>{selectedWord?.spoken || 'Select a word'}</h1>

          {selectedWord && (
            <div className="pronunciation-details">
              {pronunciationLoading ? (
                <p className="ipa">Loading pronunciation...</p>
              ) : primaryPronunciation ? (
                <>
                  <p className="ipa">{primaryPronunciation.ipa}</p>
                  {pronunciation && pronunciation.pronunciations.length > 1 && (
                    <div className="variant-selector">
                      <button
                        type="button"
                        className="variant-note"
                        aria-expanded={showPronunciations}
                        onClick={() => setShowPronunciations((value) => !value)}
                      >
                        {pronunciation.pronunciations.length} pronunciations
                        <span aria-hidden="true">{showPronunciations ? '▲' : '▼'}</span>
                      </button>

                      {showPronunciations && (
                        <div className="variant-options">
                          {pronunciation.pronunciations.map((variant, index) => (
                            <button
                              type="button"
                              key={`${variant.ipa}-${index}`}
                              className={`variant-option ${
                                selectedPronunciationIndex === index ? 'selected' : ''
                              }`}
                              onClick={() => {
                                setSelectedPronunciationIndex(index)
                                setShowPronunciations(false)
                              }}
                            >
                              <span>{variant.ipa}</span>
                              {selectedPronunciationIndex === index && (
                                <small>Selected</small>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p className="ipa">Pronunciation not found</p>
              )}
            </div>
          )}

          <div className="waveform" aria-hidden="true">
            {Array.from({ length: 22 }).map((_, index) => (
              <span key={index} style={{ height: `${16 + ((index * 17) % 34)}px` }} />
            ))}
          </div>

          <div className="controls">
            <button
              className="control"
              onClick={() => handlePlay(1)}
              disabled={!selectedWord}
            >
              <span className="control-icon">&#9654;</span>
              <strong>{t('播放', 'Play')}</strong>
              <small>{t('一般速度','Normal')}</small>
            </button>

            <button
              className="control"
              onClick={() => handlePlay(0.62)}
              disabled={!selectedWord}
            >
              <span className="control-icon">&#128034;</span>
              <strong>{t('慢速', 'Slow')}</strong>
              <small>0.62&times;</small>
            </button>

            <button
              className="control"
              onClick={handleRepeat}
              disabled={!selectedWord}
            >
              <span className="control-icon">{isSentenceRepeating ? '■' : <>&#8635;</>}</span>
              <strong>{isSentenceRepeating ? t('停止', 'Stop') : t('重複', 'Repeat')}</strong>
              {!isSentenceRepeating && <small>{t('循環','Loop')}</small>}
            </button>
          </div>

{renderPaywall()}
        </section>

        
      </section>
      <SiteFooter />
    </main>
  )
}
