import tailwindcssAnimate from 'tailwindcss-animate'
import type { Config } from 'tailwindcss'

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [tailwindcssAnimate],
} satisfies Config
