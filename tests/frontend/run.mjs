import { spawnSync } from 'node:child_process'
const compiled = spawnSync(process.execPath, ['node_modules/typescript/bin/tsc','--target','ES2022','--module','ESNext','--moduleResolution','Bundler','--jsx','react-jsx','--esModuleInterop','--skipLibCheck','--outDir','.buyer-test','src/payment/BuyerClient.ts','src/payment/BuyerPanel.tsx'], {stdio:'inherit'})
if(compiled.status) process.exit(compiled.status)
const tested=spawnSync(process.execPath,['--test','tests/frontend/buyer.test.mjs'],{stdio:'inherit',env:process.env})
if (tested.error || tested.signal || tested.status === null) { console.error('Buyer test runner terminated:', tested.signal || tested.error); process.exit(1) }
process.exit(tested.status)
