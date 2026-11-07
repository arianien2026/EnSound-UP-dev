import { consumeClaim } from './BuyerClient.js'
// Runs before React, telemetry effects, and any buyer request.
export const initialClaim = typeof window === 'undefined' ? null : consumeClaim(window.location, window.history)
