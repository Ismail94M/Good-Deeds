import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/** Q40: the HTML head carries only generic link-preview tags, the same for every page. */
describe('index.html link preview tags', () => {
  const root = resolve(__dirname, '../../..')
  const html = readFileSync(resolve(root, 'index.html'), 'utf-8')
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const og = (property: string) =>
    doc.head.querySelector(`meta[property="${property}"]`)?.getAttribute('content')

  it('has the generic Open Graph tags', () => {
    expect(og('og:title')).toBe('A good deed was shared with you')
    expect(og('og:description')).toBe('See it on Good Deeds')
    expect(og('og:site_name')).toBe('Good Deeds')
    expect(og('og:image')).toBe('/og-logo.png')
    expect(existsSync(resolve(root, 'public/og-logo.png'))).toBe(true)
  })

  it('has no robots tag in the shared HTML (the share page adds noindex itself)', () => {
    expect(doc.head.querySelector('meta[name="robots"]')).toBeNull()
  })
})
