import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ProjectListPage from '../pages/ProjectListPage'
import ProjectHome from '../pages/ProjectHome'
import Pass1Page from '../pages/Pass1Page'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5000, retry: 1 } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<ProjectListPage />} />
          <Route path="/projects/:projectId" element={<ProjectHome />} />
          <Route path="/projects/:projectId/pass1" element={<Pass1Page />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
