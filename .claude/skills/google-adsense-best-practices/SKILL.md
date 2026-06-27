---
name: google-adsense-best-practices
description: Comprehensive guidance for implementing Google AdSense in web applications using any technology stack—vanilla HTML/JavaScript, React, Next.js, Vue, Angular, or other frameworks. Covers compliance, optimization, user experience, and performance considerations.
source: https://github.com/echohtp/google-adsense-best-practices-skill
---

# AdSense Best Practices Skill

## Overview
This skill provides comprehensive guidance for implementing Google AdSense in web applications using any technology stack—vanilla HTML/JavaScript, React, Next.js, Vue, Angular, or other frameworks. It covers compliance, optimization, user experience, and performance considerations.

## Core Principles

### 1. Policy Compliance
- **Read the AdSense Program Policies**: Familiarize yourself with Google's publisher policies before implementation
- **No Invalid Traffic**: Never use bots, auto-refresh, or artificially inflate impressions/clicks
- **Content Quality**: Maintain high-quality, original content
- **Prohibited Content**: Ensure site doesn't contain adult content, violence, hateful content, or other prohibited material
- **Ad Placement Restrictions**: Don't place ads in misleading locations or hide them

### 2. Ad Placement Strategy
- **Above the Fold**: Place 1 responsive ad unit above the fold for high visibility
- **Natural Content Flow**: Integrate ads contextually within content, not forcing them
- **Spacing**: Maintain at least 6px padding around ad units
- **Content-to-Ad Ratio**: Keep 60% content / 40% ads maximum; prioritize content
- **Ad Density**: Use no more than 3 ad units per page on desktop, fewer on mobile
- **Anchor Ads**: Use only on mobile, and only 1 per page
- **In-Article Ads**: Great for long-form content; place between paragraphs, never within text
- **Sidebar Placement**: Effective for secondary content areas

### 3. Responsive Design Implementation
```javascript
// Always use responsive ad units for better performance across devices
// Responsive units automatically adjust to fit their container

// DO use responsive ads
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-xxxxxxxxxxxxxxxx"
     data-ad-slot="1234567890"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>

// DON'T use fixed-size ads for all devices
// Fixed ads can create poor UX on mobile
```

### 4. Performance Optimization
- **Lazy Loading**: Implement lazy loading for off-screen ad units to reduce initial page load
- **Async Script Loading**: Always load the Google AdSense script asynchronously
- **Minimal Render Blocking**: Avoid blocking page rendering with ad loading
- **Code Splitting**: In SPAs (React/Next.js), code-split ad loading
- **Deferred Script Execution**: Defer non-critical ad scripts

### 5. Technology-Specific Implementations

#### Vanilla HTML/JavaScript
```html
<!-- Async script loading (best practice) -->
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-xxxxxxxxxxxxxxxx"
     crossorigin="anonymous"></script>

<!-- Responsive display ad -->
<ins class="adsbygoogle"
     style="display:block"
     data-ad-client="ca-pub-xxxxxxxxxxxxxxxx"
     data-ad-slot="1234567890"
     data-ad-format="auto"
     data-full-width-responsive="true"></ins>

<!-- Push ad initialization after DOM ready -->
<script>
  (adsbygoogle = window.adsbygoogle || []).push({});
</script>
```

#### React / Next.js Implementation
```javascript
import { useEffect } from 'react';

export const AdUnit = ({ 
  adSlot, 
  format = 'auto',
  fullWidth = true,
  className = '' 
}) => {
  useEffect(() => {
    if (window.adsbygoogle === undefined) return;
    try {
      (window.adsbygoogle = window.adsbygoogle || []).push({});
    } catch (err) {
      console.error('AdSense error:', err);
    }
  }, []);

  return (
    <ins
      className={`adsbygoogle ${className}`}
      style={{ display: 'block' }}
      data-ad-client={process.env.NEXT_PUBLIC_ADSENSE_CLIENT_ID}
      data-ad-slot={adSlot}
      data-ad-format={format}
      data-full-width-responsive={fullWidth}
    />
  );
};
```

#### Vue / Nuxt Implementation
```javascript
// AdSense.vue component
export default {
  name: 'AdSense',
  props: {
    adSlot: { type: String, required: true },
    format: { type: String, default: 'auto' },
    fullWidth: { type: [Boolean, String], default: true }
  },
  data() {
    return { adClient: process.env.VUE_APP_ADSENSE_CLIENT_ID }
  },
  mounted() {
    this.$nextTick(() => {
      if (window.adsbygoogle) {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
      }
    });
  }
}
```

### 6. Environment Variables & Security
- **Never hardcode client IDs**: Use environment variables
- **Client ID structure**: `ca-pub-XXXXXXXXXXXXXXXX`

```bash
# .env.local
NEXT_PUBLIC_ADSENSE_CLIENT_ID=ca-pub-xxxxxxxxxxxxxxxx
```

### 7. Lazy Loading Implementation
```javascript
const lazyLoadAds = () => {
  if ('IntersectionObserver' in window) {
    const adObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          if (window.adsbygoogle) {
            (window.adsbygoogle = window.adsbygoogle || []).push({});
          }
          adObserver.unobserve(entry.target);
        }
      });
    });
    document.querySelectorAll('.adsbygoogle').forEach(ad => {
      adObserver.observe(ad);
    });
  }
};
```

### 8. Mobile Optimization
- **Responsive Units**: Always use responsive ad format
- **Touch-Friendly**: Ensure ads don't interfere with touch interactions
- **Viewport Meta Tag**: Include `<meta name="viewport" content="width=device-width, initial-scale=1">`
- **Anchor Ads**: Consider for mobile-specific placement (max 1 per page)
- **Mobile-First**: Test on actual mobile devices
- **Performance**: Minimize ad impact on Core Web Vitals (LCP, FID, CLS)

### 9. Troubleshooting Common Issues

| Issue | Solution |
|-------|----------|
| Ads not showing | Check Publisher ID, verify account approval, ensure browser allows scripts |
| Low CTR | Review placement, optimize content relevance, check targeting |
| High bounce rate | Ads too intrusive; adjust placement and sizing |
| CLS (Layout Shift) | Use fixed container heights for ad units, avoid dynamic resizing |
| Script errors | Ensure async script loads before ad initialization |
| Invalid traffic warnings | Review bot traffic, implement rate limiting, verify user patterns |

### 10. Best Practices Checklist
- ✓ Account approved by Google AdSense
- ✓ Responsive ad units implemented
- ✓ Async script loading enabled
- ✓ No more than 3 ad units per page
- ✓ Ads placed in contextually relevant areas
- ✓ Mobile optimization verified
- ✓ Environment variables used for credentials
- ✓ Core Web Vitals not negatively impacted
- ✓ Privacy policy updated
- ✓ GDPR/consent handling implemented
- ✓ Performance monitoring enabled
- ✓ Invalid traffic prevention measures in place

## Common Mistakes to Avoid
1. Too many ads — exceeds density limits and hurts UX
2. Misleading placement — ads disguised as content violates policies
3. Automatic refreshing — artificially increases impressions (forbidden)
4. Blocking ads with CSS
5. Encouraging clicks — never ask users to click ads
6. Testing in production — use AdSense Test Publisher ID for development
7. Fixed ad sizes only — mobile users get poor experience
8. Ignoring analytics
9. Poor content quality — low-quality sites get lower CPM
10. Hardcoding credentials — security risk if code is public
