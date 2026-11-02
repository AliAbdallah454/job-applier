# Future features

Ideas that are not built yet. Move a feature out of this file when it is built. Work left out of
something already built goes in [skipped-work.md](skipped-work.md).

Each feature has one row per part:

- **Value:** how much it would help an application. High, medium or low.
- **Possible:** whether it can be done within the rules of the services involved.
- **Difficulty:** how much work it is here. Low, medium or high.

## See recruiters at the company

Show who works in hiring at a company next to its job: their name, title, LinkedIn profile and work
email. The app only shows this information. The user decides whether to contact anyone and does it
themselves, so the app never sends a message.

| Part | Value | Possible | Difficulty | Difficulties |
|---|---|---|---|---|
| **Contact panel:** on a job, list a company's people with their title, email and LinkedIn link from a data service such as Hunter or Apollo | High | Yes, under that service's terms, which need checking | Medium | Costs money or credits, so look up on a button press and keep the result for that company. Jobs hold a company name, and these services work best with its web domain. Results can be wrong or old, so show the service's confidence. Small companies may have few results. GDPR applies to EU contacts, more so with other users |
| **"Find people" links:** a job button opens a LinkedIn people search filled in with the company and "recruiter", "talent" or "engineering manager" | High | Yes. The user searches as themselves and nothing is scraped or stored | Low | Results depend on how the company name matches on LinkedIn. Shows links, not details in the app |
| **Contacts in the job post:** show a recruiter's name or email that the post already gives | Medium | Yes. It is what the company published | Low | Few posts have one |
| **LinkedIn profiles from a search engine:** use a search API to find public profile links for a company's recruiters | Medium | Probably, under that API's terms, which need checking | Medium | Results are noisy and can show people who have left. Gives a link and a title, not an email |
| **Scraping LinkedIn:** read profiles from LinkedIn automatically | Low | **No.** LinkedIn's terms forbid it and it detects it | High | The user's own account can be banned. **Do not build this** |

**Build first:** the "Find people" links, as they are free and quick, then the contact panel, which
is what shows names and emails in the app. Start once login is confirmed working.
