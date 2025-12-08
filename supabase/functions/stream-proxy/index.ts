import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type, range',
  'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges',
}

serve(async (req) => {
  // Gestion du pre-flight CORS
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders })
  }

  try {
    const url = new URL(req.url)
    const targetUrl = url.searchParams.get('url')

    if (!targetUrl) {
      return new Response('Missing url param', { status: 400, headers: corsHeaders })
    }

    // On transmet le header Range pour permettre l'avance rapide (seeking)
    const headers = new Headers()
    const range = req.headers.get('range')
    if (range) {
      headers.set('range', range)
    }

    // On récupère le flux depuis la source (Qobuz/Subsonic)
    const response = await fetch(targetUrl, { headers })

    // On prépare les headers de réponse
    const newHeaders = new Headers(response.headers)
    
    // On s'assure que les headers CORS sont présents
    Object.entries(corsHeaders).forEach(([key, value]) => {
      newHeaders.set(key, value)
    })

    // On renvoie le flux directement (piping)
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    })

  } catch (error) {
    return new Response(JSON.stringify({ error: error.message }), {
      status: 500,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    })
  }
})