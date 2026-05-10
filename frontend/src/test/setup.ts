import '@testing-library/jest-dom'
import { configure } from '@testing-library/react'

// Reduce waitFor timeout from the 1000ms default so failing tests don't stall.
// 200ms is enough for mocked async resolution while keeping the suite fast.
configure({ asyncUtilTimeout: 200 })
