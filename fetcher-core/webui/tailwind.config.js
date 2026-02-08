/**
 * @type {import('tailwindcss').Config}
 * Tailwind CSS configuration file.
 * 
 * - `darkMode`: Enables dark mode using the `class` strategy.
 * - `content`: Specifies the paths to all template files in the project
 *   to ensure unused styles are purged in production.
 * - `theme`: Allows for extending the default Tailwind CSS theme.
 * - `plugins`: An array to include additional Tailwind CSS plugins.
 */
module.exports = {
  darkMode: 'class', // Enable class-based dark mode
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [
    css: {
      postcss: {
          plugins: [tailwindcss()],
      },   
  }, 
  ],
};
