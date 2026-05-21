import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import JobDetail from './pages/JobDetail'
import Jobs from './pages/Jobs'
import RunDetail from './pages/RunDetail'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
