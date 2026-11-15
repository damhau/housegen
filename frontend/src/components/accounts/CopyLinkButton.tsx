import { useState } from "react"
import { Check, Copy } from "lucide-react"
import { Button } from "@/components/ui/button"
import { absoluteUrl } from "@/lib/auth"

/** Copies an invitation link (the API returns it relative); a prompt shows it where the clipboard is refused. */
export function CopyLinkButton({ url, label = "Copy link" }: { url: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    const link = absoluteUrl(url)
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      window.prompt("Copy the invitation link", link)
    }
  }
  return (
    <Button variant="outline" size="sm" onClick={() => void copy()}>
      {copied ? <Check /> : <Copy />}
      {copied ? "Copied" : label}
    </Button>
  )
}
