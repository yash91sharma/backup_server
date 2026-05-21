import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import Layout from './Layout'
import { renderWithProviders, screen, userEvent } from '../test/utils'

/**
 * Layout owns the sidebar + content frame. These tests exercise the full
 * nested-route shape (`<Route element={<Layout/>}>...child routes...</Route>`)
 * so we catch regressions in how the Outlet is wired.
 */
function renderRoutes(initialRoute: string) {
  return renderWithProviders(
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<div>HOME PAGE</div>} />
        <Route path="/jobs" element={<div>JOBS PAGE</div>} />
        <Route path="/settings" element={<div>SETTINGS PAGE</div>} />
      </Route>
    </Routes>,
    { route: initialRoute }
  )
}

describe('Layout', () => {
  it('renders the sidebar nav alongside the routed page', () => {
    renderRoutes('/')
    expect(screen.getByRole('navigation', { name: /primary/i })).toBeInTheDocument()
    expect(screen.getByText('HOME PAGE')).toBeInTheDocument()
  })

  it('clicking a nav link routes to the matching page', async () => {
    renderRoutes('/')
    await userEvent.click(screen.getByRole('link', { name: /jobs/i }))
    expect(await screen.findByText('JOBS PAGE')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: /settings/i }))
    expect(await screen.findByText('SETTINGS PAGE')).toBeInTheDocument()
  })

  it('toggle button collapses and re-expands the sidebar', async () => {
    renderRoutes('/')
    const sidebar = screen.getByRole('complementary', { name: /sidebar/i })
    const toggle = screen.getByRole('button', { name: /toggle navigation|collapse|expand/i })

    // Start expanded — sidebar carries data-expanded="true"
    expect(sidebar).toHaveAttribute('data-expanded', 'true')

    await userEvent.click(toggle)
    expect(sidebar).toHaveAttribute('data-expanded', 'false')

    await userEvent.click(toggle)
    expect(sidebar).toHaveAttribute('data-expanded', 'true')
  })
})
