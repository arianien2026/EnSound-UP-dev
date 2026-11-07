import { useEffect, useState } from 'react'
import { BuyerClient, STAGE } from './BuyerClient.js'
export const messages: Record<string, [string, string]> = {
  free: ['目前使用免費版。已購買者可要求啟用信。', 'Free access. Already purchased? Request an access email.'],
  checking: ['正在驗證使用權，暫以免費版使用。', 'Checking access; Free remains available.'],
  verify_email: ['請開啟驗證信，再返回此頁確認 Email。', 'Open your verification email, then check again here.'],
  ready_checkout: ['Email 已驗證，可前往綠界測試結帳。', 'Email verified. ECPay Stage checkout is available.'],
  pending: ['付款確認中，尚未開通完整版。', 'Payment pending. Full Access has not been activated.'],
  paid_claim: ['後端已確認付款。請要求啟用信以授權此裝置。', 'Payment confirmed by the backend. Request an access email for this device.'],
  claim_sent: ['若符合資格，將寄送啟用信；重寄需間隔 10 分鐘。', 'If eligible, an access email will arrive. Allow 10 minutes before resending.'],
  active: ['此裝置已由後端驗證，完整版已啟用。', 'This device is verified. Full Access is active.'],
  denied: ['使用權無效或已撤銷，已回到免費版。', 'Access is invalid or revoked. Free access restored.'],
  expired: ['裝置憑證已過期，請重新要求啟用信。', 'Device credential expired. Request a new access email.'],
  link_expired: ['啟用連結或驗證階段已失效，請重新取得啟用信。', 'Access link or claim session expired. Request a new access email.'],
  device_limit: ['已達 3 台裝置上限，無法啟用此裝置。', 'Three-device limit reached. This device was not activated.'],
  unavailable: ['目前無法完成，請稍後重試；不會因此開通完整版。', 'Unavailable. Please retry; Full Access remains locked.'],
  email_mismatch: ['兩次輸入的 Email 必須相同。', 'Both email entries must match.'],
  order_closed: ['此訂單目前無法結帳，請確認訂單或聯絡客服。', 'This order cannot be checked out. Review it or contact support.'],
}
export function submitCheckout(data: { checkout_url: string; fields: Record<string, string> }, doc: Document = document) {
  if (data.checkout_url !== STAGE) throw new Error('Invalid checkout target')
  const form = doc.createElement('form'); form.method = 'POST'; form.action = STAGE
  form.target = '_self'; form.rel = 'noopener noreferrer'; form.hidden = true
  for (const [name, value] of Object.entries(data.fields)) {
    const input = doc.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.appendChild(input)
  }
  doc.body.appendChild(form)
  try { form.submit() } finally { form.remove() }
}
export function BuyerPanel({ client }: { client: BuyerClient }) {
  const [state, setState] = useState(client.state)
  const [email, setEmail] = useState(''); const [confirmation, setConfirmation] = useState('')
  useEffect(() => { setState(client.state); return client.subscribe(() => setState(client.state)) }, [client])
  const msg = messages[state.message] || messages.unavailable
  return <section className="buyer-panel" aria-label="購買與啟用 / Purchase and access">
    <h3>購買與啟用 / Purchase and access</h3>
    <p>Development · ECPay Stage（測試付款）</p>
    <p role="status" aria-live="polite">{msg[0]}<br />{msg[1]}</p>
    <label>Email<input type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
    <label>再次確認 Email / Confirm email<input type="email" autoComplete="off" value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label>
    <button type="button" disabled={state.busy || !email || email !== confirmation} onClick={() => void client.createOrder(email, confirmation)}>建立訂單並寄驗證信 / Create order</button>
    {state.amount !== null && <p>後端訂單金額 / Order total: NT${state.amount}</p>}
    {state.orderId && <><button type="button" disabled={state.busy} onClick={() => void client.refreshOrder()}>確認 Email／付款狀態 / Check status</button>
      <button type="button" disabled={state.busy || !state.emailVerified || state.orderStatus !== 'pending'} onClick={async () => {
        const data = await client.checkout(); if (data) submitCheckout(data)
      }}>綠界測試結帳 / ECPay Stage checkout</button></>}
    <button type="button" disabled={state.busy || !email} onClick={() => void client.claimEmail(email)}>要求啟用信 / Request access email</button>
    <button type="button" disabled={state.busy} onClick={() => void client.revalidate()}>重新驗證此裝置 / Recheck this device</button>
    <p>付款返回頁面不會自動授權；啟用信需在 15 分鐘內使用。<br />Returning from checkout does not grant access. Use access email within 15 minutes.</p>
  </section>
}
