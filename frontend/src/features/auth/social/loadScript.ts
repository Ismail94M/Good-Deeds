const loaded = new Map<string, Promise<void>>()

/** Load an external script once. Rejects if it fails to load (offline, blocked by an extension). */
export function loadScript(src: string): Promise<void> {
  const existing = loaded.get(src)
  if (existing) return existing
  const promise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.async = true
    script.defer = true
    script.onload = () => resolve()
    script.onerror = () => {
      loaded.delete(src)
      script.remove()
      reject(new Error(`Could not load ${src}`))
    }
    document.head.appendChild(script)
  })
  loaded.set(src, promise)
  return promise
}
