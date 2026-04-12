import { describe, expect, it, vi } from 'vitest'
import { resolveBootstrapCity } from '../composables/bootstrapCity'

describe('resolveBootstrapCity', () => {
  it('loads the route city immediately when one is specified', async () => {
    const resolveSharedLocationCity = vi.fn(async () => 'USBOS')

    const result = await resolveBootstrapCity({
      routeCity: 'USSFO',
      defaultCity: 'USSFO',
      resolveSharedLocationCity,
    })

    expect(result).toEqual({ city: 'USSFO', source: 'route' })
    expect(resolveSharedLocationCity).not.toHaveBeenCalled()
  })

  it('waits for shared location and loads the closest city when no route city is specified', async () => {
    const resolveSharedLocationCity = vi.fn(async () => 'USBOS')

    const result = await resolveBootstrapCity({
      routeCity: null,
      defaultCity: 'USSFO',
      resolveSharedLocationCity,
    })

    expect(result).toEqual({ city: 'USBOS', source: 'shared-location' })
    expect(resolveSharedLocationCity).toHaveBeenCalledTimes(1)
  })

  it('falls back to the default city when there is no route city and no shared location', async () => {
    const resolveSharedLocationCity = vi.fn(async () => null)

    const result = await resolveBootstrapCity({
      routeCity: null,
      defaultCity: 'USSFO',
      resolveSharedLocationCity,
    })

    expect(result).toEqual({ city: 'USSFO', source: 'default' })
    expect(resolveSharedLocationCity).toHaveBeenCalledTimes(1)
  })
})