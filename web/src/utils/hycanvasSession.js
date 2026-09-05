const isLoopbackHostname = (hostname) => (
  hostname === 'localhost' || hostname === '[::1]' || /^127(?:\.\d{1,3}){3}$/.test(hostname)
)

export const normalizeHyCanvasSessionUrl = (sessionUrl, appUrl) => {
  const target = new URL(sessionUrl)
  const app = new URL(appUrl)
  if (isLoopbackHostname(target.hostname) && isLoopbackHostname(app.hostname)) {
    target.hostname = app.hostname
  }
  return target.toString()
}
