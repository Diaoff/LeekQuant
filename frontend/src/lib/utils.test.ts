import { describe, it, expect } from 'vitest'
import { formatNumber, formatDuration, formatMarketCap } from './utils'

describe('formatNumber', () => {
  it('formats with zh-CN thousands grouping', () => {
    expect(formatNumber(1234567.891, 2)).toBe('1,234,567.89')
  })

  it('returns 暂无 for null/undefined/empty', () => {
    expect(formatNumber(null)).toBe('暂无')
    expect(formatNumber(undefined)).toBe('暂无')
    expect(formatNumber('')).toBe('暂无')
  })

  it('returns 暂无 for non-finite input', () => {
    expect(formatNumber(NaN)).toBe('暂无')
    expect(formatNumber('abc')).toBe('暂无')
  })
})

describe('formatDuration', () => {
  it('formats milliseconds into HH:MM:SS', () => {
    expect(formatDuration(3661000)).toBe('01:01:01')
  })

  it('returns 进行中 for null/undefined', () => {
    expect(formatDuration(null)).toBe('进行中')
    expect(formatDuration(undefined)).toBe('进行中')
  })

  it('clamps negative durations to 00:00:00', () => {
    expect(formatDuration(-5000)).toBe('00:00:00')
  })
})

describe('formatMarketCap', () => {
  it('formats 亿 above 1e8', () => {
    expect(formatMarketCap('1500000000')).toBe('15 亿')
  })

  it('formats 万 above 1e4', () => {
    expect(formatMarketCap('25000')).toBe('2.5 万')
  })

  it('falls back to plain number below 1e4', () => {
    expect(formatMarketCap('999')).toBe('999')
  })

  it('handles non-finite input', () => {
    expect(formatMarketCap('abc')).toBe('暂无')
  })
})
