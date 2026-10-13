/**
 * VaultDoc ships as a single self-contained `index.html` (Tailwind CDN + Lucide
 * CDN + vanilla ES6) so FastAPI can serve it directly with no build step.
 *
 * This React entry point exists only for `vite dev` convenience: it renders the
 * standalone file inside a frame so the same code can be reviewed with a hot
 * reload server. The production build emits the standalone file itself.
 */
export default function App() {
  return (
    <div className="fixed inset-0 overflow-hidden bg-[#09090b]">
      <iframe title="VaultDoc" src="/index.html" className="h-full w-full border-0" />
    </div>
  );
}
