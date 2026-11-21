import { useEffect, useState } from 'react'
import { BuyerClient, type PaymentMode } from './BuyerClient.js'
import { initialClaim } from './bootstrap.js'
export function useBuyer(enabled = true) {
  const [client] = useState(() => {
    const mode = (import.meta.env.VITE_ENSOUND_PAYMENT_ENV ?? 'stage') as PaymentMode
    const api = import.meta.env.VITE_ENSOUND_API_BASE_URL || 'https://ensound-up-dev.onrender.com'
    return new BuyerClient(api, window.localStorage, undefined, undefined, undefined, mode)
  })
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
