import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './App.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
<div className="development-build-bar">
  🧪 EnSound UP · Development
</div>
    <App />
  </StrictMode>,
)
