import { Toaster as Sonner, toast } from "sonner"

type ToasterProps = React.ComponentProps<typeof Sonner>

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-card group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          // Muted error style - dark red-tinted background with readable text
          error: "group-[.toaster]:bg-[#2a1a1a] group-[.toaster]:text-[#e8a0a0] group-[.toaster]:border-[#4a2a2a] [&_[data-description]]:text-[#b08080]",
        },
      }}
      {...props}
    />
  )
}

export { Toaster, toast }
