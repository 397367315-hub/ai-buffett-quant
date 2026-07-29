/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0D1117',
        card: '#161B22',
        border: '#30363D',
        text: '#E6EDF3',
        'text-secondary': '#8B949E',
        up: '#EF5350',
        down: '#26A69A',
        accent: '#58A6FF',
        warn: '#D29922',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        sans: ['PingFang SC', 'Microsoft YaHei', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
