import { useEffect, useState } from 'react'
import { BuyerClient } from './BuyerClient.js'
import { initialClaim } from './bootstrap.js'
export function useBuyer(enabled = true) {
  const [client] = useState(() => new BuyerClient(import.meta.env.VITE_ENSOUND_API_BASE_URL || 'https://ensound-up-dev.onrender.com', window.localStorage))
  const [full, setFull] = useState(false)
  useEffect(() => {
    if (!enabled) return
    const unsubscribe = client.subscribe(() => setFull(client.state.full))
    void client.start(initialClaim)
    const check = () => { if (document.visibilityState !== 'hidden') { void client.revalidate(); void client.refreshOrder() } }
    const visibility = () => { if (document.visibilityState === 'hidden') client.suspend(); else check() }
    const offline = () => client.suspend()
    window.addEventListener('offline', offline); window.addEventListener('online', check)
    window.addEventListener('focus', check); document.addEventListener('visibilitychange', visibility)
    const timer = window.setInterval(check, 60000)
    return () => { unsubscribe(); window.clearInterval(timer); window.removeEventListener('offline', offline); window.removeEventListener('online', check); window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', visibility) }
  }, [client, enabled])
  return { client, full }
}
