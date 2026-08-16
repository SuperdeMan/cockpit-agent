import baseConfig from './vite.config.ts'

const tailnetHost = process.env.__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS?.trim()

if (!tailnetHost) {
  throw new Error('__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS is required')
}

export default {
  ...baseConfig,
  server: {
    ...baseConfig.server,
    allowedHosts: [tailnetHost],
  },
}
