import { InteractionRequiredAuthError, PublicClientApplication, type AccountInfo, type Configuration } from '@azure/msal-browser'

const msalClientId = ((import.meta.env.VITE_MSAL_CLIENT_ID as string | undefined) || '').trim()
const msalTenantId = ((import.meta.env.VITE_MSAL_TENANT_ID as string | undefined) || '').trim()
const msalScope = ((import.meta.env.VITE_MSAL_SCOPE as string | undefined) || '').trim()

export const msalEnabled = Boolean(msalClientId && msalTenantId && msalScope)

const config: Configuration = {
  auth: {
    clientId: msalClientId || 'dev-client-id',
    authority: `https://login.microsoftonline.com/${msalTenantId || 'common'}`,
    redirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: 'localStorage',
  },
}

export const msalInstance = new PublicClientApplication(config)
export const loginRequest = {
  scopes: msalScope ? [msalScope] : [],
}

let msalInitialized = false

export async function ensureMsalInitialized() {
  if (!msalEnabled || msalInitialized) return
  await msalInstance.initialize()
  await msalInstance.handleRedirectPromise()
  msalInitialized = true
}

export async function tryMicrosoftSso() {
  if (!msalEnabled) return ''
  await ensureMsalInitialized()
  const accounts = msalInstance.getAllAccounts()
  if (accounts.length) {
    const token = await acquireForAccount(accounts[0])
    if (token) return token
  }
  try {
    const result = await msalInstance.ssoSilent(loginRequest)
    return result.accessToken
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) return ''
    return ''
  }
}

export async function signInWithMicrosoft() {
  if (!msalEnabled) throw new Error('Microsoft Entra sign-in is not configured')
  await ensureMsalInitialized()
  const result = await msalInstance.loginPopup(loginRequest)
  return result.accessToken || (await acquireForAccount(result.account))
}

async function acquireForAccount(account?: AccountInfo | null) {
  if (!account) return ''
  try {
    const result = await msalInstance.acquireTokenSilent({ ...loginRequest, account })
    return result.accessToken
  } catch {
    return ''
  }
}
