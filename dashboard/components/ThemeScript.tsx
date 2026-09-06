import Script from "next/script";

/** Apply stored theme before paint to avoid flash. */
export function ThemeScript() {
  const code = `(function(){try{var t=localStorage.getItem("econith-theme");var d=t!=="light";document.documentElement.classList.toggle("dark",d);document.documentElement.classList.toggle("light",!d);document.documentElement.style.colorScheme=d?"dark":"light";}catch(e){document.documentElement.classList.add("dark");}})();`;
  return (
    <Script id="econith-theme-init" strategy="beforeInteractive">
      {code}
    </Script>
  );
}
