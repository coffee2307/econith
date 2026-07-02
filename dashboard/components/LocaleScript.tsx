/** Inline script to apply stored locale before paint. */
export function LocaleScript() {
  const code = `(function(){try{var l=localStorage.getItem("econith-locale");if(l!=="en"&&l!=="vi"){l=(navigator.language||"").toLowerCase().startsWith("vi")?"vi":"en";}var r=document.documentElement;r.lang=l==="vi"?"vi":"en";r.dataset.locale=l;}catch(e){document.documentElement.lang="en";document.documentElement.dataset.locale="en";}})();`;
  return (
    <script
      dangerouslySetInnerHTML={{ __html: code }}
      suppressHydrationWarning
    />
  );
}
