import { describe, expect, it } from 'vitest'
import { normalizeHyCanvasSessionUrl } from '../hycanvasSession'

describe('normalizeHyCanvasSessionUrl', () => {
  it('uses the ContentSwarm loopback hostname for the HyCanvas iframe', () => {
    const result = normalizeHyCanvasSessionUrl(
      'http://127.0.0.1:8005/api/v1/auth/integration?ticket=test&next=%2Fdashboard%2F',
      'http://localhost:5173/hycanvas'
    )

    expect(result).toBe(
      'http://localhost:8005/api/v1/auth/integration?ticket=test&next=%2Fdashboard%2F'
    )
  })

  it('does not rewrite a configured remote HyCanvas host', () => {
    expect(
      normalizeHyCanvasSessionUrl(
        'https://canvas.example/api/v1/auth/integration?ticket=test',
        'http://localhost:5173/hycanvas'
      )
    ).toBe('https://canvas.example/api/v1/auth/integration?ticket=test')
  })
})
