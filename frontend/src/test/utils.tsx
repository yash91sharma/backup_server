import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import React from 'react'
import { MemoryRouter } from 'react-router-dom'

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}

interface WrapperOptions extends RenderOptions {
  route?: string
}

export function renderWithProviders(
  ui: React.ReactElement,
  { route = '/', ...options }: WrapperOptions = {}
) {
  const queryClient = makeQueryClient()
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </MemoryRouter>,
    options
  )
}

export { screen, waitFor, within, act } from '@testing-library/react'
export { userEvent } from '@testing-library/user-event'
