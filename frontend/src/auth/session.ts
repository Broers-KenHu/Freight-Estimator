const ACCESS_TOKEN_KEY = 'freight_access_token'

const storage = () => {
  if (typeof window === 'undefined' || !window.localStorage) return null
  return window.localStorage
}

export const getAccessToken = () => {
  try {
    return storage()?.getItem(ACCESS_TOKEN_KEY) || null
  } catch {
    return null
  }
}

export const setAccessToken = (token: string) => {
  try {
    storage()?.setItem(ACCESS_TOKEN_KEY, token)
  } catch {
    // Ignore storage failures; the login request itself still completed.
  }
}

export const clearAccessToken = () => {
  try {
    storage()?.removeItem(ACCESS_TOKEN_KEY)
  } catch {
    // Ignore storage failures during logout or token reset.
  }
}
