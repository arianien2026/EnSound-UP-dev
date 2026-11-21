export const PRODUCT = 'ensound_up_web_full_access'
export const STAGE = 'https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5'
export const PRODUCTION = 'https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5'
export type PaymentMode = 'stage' | 'production'
export const DEVICE_KEY = 'ensound-dev-device-v1'
const ORDER_KEY = 'ensound-dev-pending-order-v1'
const DEVICE_ID_KEY = 'ensound-dev-device-id-v1'
const tokenPattern = /^[A-Za-z0-9_-]{43}$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
export type BuyerState = { full: boolean; busy: boolean; message: string; orderId: string | null;
  orderStatus: string; emailVerified: boolean; amount: number | null }
type Device = { deviceId: string; credential: string; expiresAt: string }
export class BuyerError extends Error { constructor(public status: number) { super('Buyer request unavailable') } }
export function consumeClaim(location: Pick<Location, 'hash' | 'pathname' | 'search'>,
  history: Pick<History, 'replaceState'>): string | null {
  const params = new URLSearchParams(location.hash.slice(1))
  const value = params.get('access_claim')
  if (value !== null) history.replaceState(null, '', location.pathname + location.search)
  return value === null ? null : tokenPattern.test(value) ? value : 'invalid'
}
export class BuyerClient {
  private listeners = new Set<() => void>()
  private revision = 0
  private booted = false
  private expiryTimer: ReturnType<typeof setTimeout> | undefined
  state: BuyerState = { full: false, busy: false, message: 'free', orderId: null,
    orderStatus: '', emailVerified: false, amount: null }
  constructor(private base: string, private store: StorageLike,
    private fetcher: typeof fetch = fetch, private now = () => Date.now(),
    private newId = () => crypto.randomUUID(), readonly paymentMode: PaymentMode = 'stage') {
    const u = new URL(base)
    const development = u.origin === 'https://ensound-up-dev.onrender.com' ||
      (u.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(u.hostname))
    const production = u.protocol === 'https:' && u.hostname.includes('.') &&
      !u.port && u.hostname !== 'ensound-up-dev.onrender.com'
    if (!(paymentMode === 'stage' ? development : paymentMode === 'production' && production) ||
        u.pathname !== '/' || u.search || u.hash || u.username || u.password) throw new Error('Invalid payment API')
    this.base = u.origin
    const orderId = this.read(ORDER_KEY)
    if (orderId && uuidPattern.test(orderId)) this.state.orderId = orderId
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  private update(values: Partial<BuyerState>) { this.state = { ...this.state, ...values }; this.listeners.forEach(fn => fn()) }
  private storageKey(key: string) { return this.paymentMode === 'stage' ? key : 'production:' + key }
  private async purchaseKey(email: string): Promise<string> {
    const split = email.lastIndexOf('@')
    const normalized = email.slice(0, split + 1) + email.slice(split + 1).toLowerCase()
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(normalized))
    const key = 'ensound-purchase-v1:' + this.base + ':' + Array.from(
      new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0'),
    ).join('')
    const reuseOrCreate = () => {
      const value = this.read(key)
      const now = this.now()
      if (value) {
        const saved = JSON.parse(value)
        if (uuidPattern.test(saved.id) && Number.isFinite(saved.until) && saved.until > now) return saved.id as string
      }
      const id = crypto.randomUUID()
      this.write(key, JSON.stringify({id, until: now + 2 * 60 * 60 * 1000}))
      return id
    }
    if (globalThis.navigator?.locks) return navigator.locks.request(this.storageKey(key), reuseOrCreate)
    // A browser without shared locking cannot guarantee safe tab-to-tab retries.
    if (typeof window !== 'undefined') throw new Error('Shared purchase storage unavailable')
    return reuseOrCreate() // Isolated runtimes run their clients on one event loop.
  }
  private read(key: string) { try { return this.store.getItem(this.storageKey(key)) } catch { return null } }
  private write(key: string, value: string) { this.store.setItem(this.storageKey(key), value) }
  private remove(key: string) { try { this.store.removeItem(this.storageKey(key)) } catch { /* remain FREE */ } }
  private async request(path: string, body?: unknown, headers?: Record<string, string>) {
    // Native browser fetch must receive Window, not the BuyerClient instance.
    const response = await this.fetcher.call(globalThis, this.base + path, { method: body === undefined ? 'GET' : 'POST',
      cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(10000) })
    if (!response.ok) throw new BuyerError(response.status)
    return response.json()
  }
  private device(): Device | null {
    try {
      const d = JSON.parse(this.read(DEVICE_KEY) || 'null')
      return d && uuidPattern.test(d.deviceId) && tokenPattern.test(d.credential) &&
        Number.isFinite(Date.parse(d.expiresAt)) ? d : null
    } catch { return null }
  }
  private deny(message: string) {
    ++this.revision
    clearTimeout(this.expiryTimer)
    this.update({ full: false, message })
  }
  async start(claim: string | null = null) {
    if (this.booted) return
    this.booted = true
    if (claim) await this.acceptClaim(claim)
    else await this.revalidate()
    if (this.state.orderId) await this.refreshOrder()
  }
  async revalidate() {
    const rev = ++this.revision
    clearTimeout(this.expiryTimer)
    const d = this.device()
    this.update({ full: false }) // Never trust a persisted flag or a cached authorization.
    if (!d) { this.update({ message: 'free' }); return }
    if (Date.parse(d.expiresAt) <= this.now()) { this.remove(DEVICE_KEY); this.update({ message: 'expired' }); return }
    try {
      const data = await this.request('/access/status', undefined,
        { Authorization: 'Bearer ' + d.credential, 'X-Device-Id': d.deviceId })
      if (rev !== this.revision) return
      const expiry = Date.parse(data.credential_expires_at)
      if (data.status !== 'active' || data.product_code !== PRODUCT || !Number.isFinite(expiry) || expiry <= this.now()) throw new BuyerError(401)
      this.update({ full: true, message: 'active' })
      clearTimeout(this.expiryTimer)
      this.expiryTimer = setTimeout(() => { this.deny('expired') }, Math.min(expiry - this.now(), 2147483647))
    } catch (error) {
      if (rev !== this.revision) return
      if (error instanceof BuyerError && [401, 403].includes(error.status)) this.remove(DEVICE_KEY)
      this.update({ full: false, message: error instanceof BuyerError && [401, 403].includes(error.status) ? 'denied' : 'unavailable' })
    }
  }
  async createOrder(email: string, confirmation: string) {
    if (this.state.busy) return
    if (!email.trim() || email.trim() !== confirmation.trim()) { this.update({ message: 'email_mismatch' }); return }
    this.update({ busy: true })
    try {
      const idempotency_key = await this.purchaseKey(email.trim())
      const data = await this.request('/orders', { email: email.trim(), idempotency_key })
      if (!uuidPattern.test(data.order_id) || !Number.isInteger(data.amount) || data.amount <= 0 || data.currency !== 'TWD' || !['pending', 'paid', 'expired', 'cancelled', 'refund_pending', 'refunded'].includes(data.status)) throw new Error()
      this.write(ORDER_KEY, data.order_id)
      this.update({ orderId: data.order_id, orderStatus: data.status, emailVerified: false, amount: data.amount, message: 'verify_email' })
      if (data.status !== 'pending') await this.refreshOrder()
    } catch { this.update({ message: 'unavailable' }) }
    finally { this.update({ busy: false }) }
  }
  async refreshOrder() {
    if (!this.state.orderId) return
    try {
      const data = await this.request('/orders/' + this.state.orderId + '/status')
      if (typeof data.email_verified !== 'boolean' || !['pending', 'expired', 'paid', 'refund_pending', 'refunded', 'cancelled'].includes(data.status)) throw new Error()
      this.update({ emailVerified: data.email_verified, orderStatus: data.status,
        ...(this.state.full ? {} : { message: data.status === 'paid' ? 'paid_claim' : data.email_verified && data.status === 'pending' ? 'ready_checkout' : data.status === 'pending' ? 'verify_email' : 'order_closed' }) })
    } catch { this.update({ message: 'unavailable' }) }
  }
  async checkout(): Promise<{ checkout_url: string; fields: Record<string, string> } | null> {
    if (this.state.busy || !this.state.orderId) return null
    this.update({ busy: true })
    try {
      const data = await this.request('/orders/' + this.state.orderId + '/checkout', {})
      if (data.checkout_url !== (this.paymentMode === 'production' ? PRODUCTION : STAGE) || !data.fields || Object.values(data.fields).some(v => typeof v !== 'string') ||
          data.fields.MerchantTradeNo === undefined || data.fields.CheckMacValue === undefined || 'HashKey' in data.fields || 'HashIV' in data.fields) throw new Error()
      // Checkout is only navigation; it never changes verified access.
      this.update({ message: 'pending' })
      return data
    } catch (error) { this.update({ message: error instanceof BuyerError && error.status === 409 ? 'verify_email' : 'unavailable' }); return null }
    finally { this.update({ busy: false }) }
  }
  async claimEmail(email: string) {
    this.update({ busy: true })
    try { await this.request('/access/email-links', { email: email.trim() }); this.update({ message: 'claim_sent' }) }
    catch { this.update({ message: 'unavailable' }) }
    finally { this.update({ busy: false }) }
  }
  async acceptClaim(proof: string) {
    this.deny('checking')
    if (!tokenPattern.test(proof)) { this.update({ message: 'link_expired' }); return }
    this.update({ busy: true })
    try {
      let deviceId = this.read(DEVICE_ID_KEY)
      if (!deviceId || !uuidPattern.test(deviceId)) { deviceId = this.newId(); this.write(DEVICE_ID_KEY, deviceId) }
      const data = await this.request('/access/devices', { claim_session: proof, device_id: deviceId })
      if (!tokenPattern.test(data.device_credential) || Date.parse(data.credential_expires_at) <= this.now() || !Number.isFinite(Date.parse(data.credential_expires_at))) throw new Error()
      this.write(DEVICE_KEY, JSON.stringify({ deviceId, credential: data.device_credential, expiresAt: data.credential_expires_at }))
      await this.revalidate()
      if (this.state.full) { this.remove(ORDER_KEY); this.update({ orderId: null, orderStatus: '', amount: null }) }
    } catch (error) {
      this.update({ full: false, message: error instanceof BuyerError && error.status === 409 ? 'device_limit' :
        error instanceof BuyerError && [400, 401, 403].includes(error.status) ? 'link_expired' : 'unavailable' })
    } finally { this.update({ busy: false }) }
  }
  suspend() { this.deny('checking') }
  dispose() { ++this.revision; clearTimeout(this.expiryTimer) }
}
