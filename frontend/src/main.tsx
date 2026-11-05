import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'
import { ApiError } from './api/client'

// A 4xx is the server's final answer (a deleted project, a stale link): show
// it at once. Network errors, 5xx, 408 and 429 keep the default three retries.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          const { status } = error
          if (status >= 400 && status < 500 && status !== 408 && status !== 429) return false
        }
        return failureCount < 3
      },
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
