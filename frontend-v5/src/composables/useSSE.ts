import { ref } from 'vue'

export type SSEMessage = Record<string, unknown>

/**
 * POST 型 SSE（用于 search / analysis / scan）
 */
export function usePostSSE() {
  const loading = ref(false)
  let controller: AbortController | null = null

  function abort() {
    controller?.abort()
    controller = null
    loading.value = false
  }

  async function start(
    url: string,
    body: unknown,
    onMessage: (data: SSEMessage) => void,
    onDone?: () => void,
    onError?: (err: Error) => void,
  ) {
    abort()
    loading.value = true
    controller = new AbortController()

    try {
      const res = await fetch(`/api${url}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''
        for (const part of parts) {
          const line = part.replace(/^data:\s*/, '').trim()
          if (!line) continue
          try { onMessage(JSON.parse(line)) } catch { /* skip */ }
        }
      }
      onDone?.()
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        onError?.(err)
      }
    } finally {
      loading.value = false
      controller = null
    }
  }

  return { loading, start, abort }
}

/**
 * GET 型 SSE（用于全局事件流 /api/events）
 */
export function useGetSSE() {
  let es: EventSource | null = null

  function open(
    url: string,
    onMessage: (data: SSEMessage) => void,
    onError?: (e: Event) => void,
  ) {
    close()
    es = new EventSource(url)
    es.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data as string)) } catch { /* skip */ }
    }
    if (onError) es.onerror = onError
  }

  function close() {
    es?.close()
    es = null
  }

  return { open, close }
}
