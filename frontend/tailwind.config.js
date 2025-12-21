import preset from './design-system/tailwind.preset.js'

/** @type {import('tailwindcss').Config} */
export default {
  presets: [preset],
  content: [
    './index.html',
    './app.js',
    './src/**/*.{js,css}'
  ]
}
