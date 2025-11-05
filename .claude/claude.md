# Claude Code Project Guidelines

## Documentation Structure

### Root Directory - User-Facing Documentation ONLY
The main `Flamingo_Control/` directory should contain **only** documentation that end users need:
- `README.md` - Project overview and quick start
- `INSTALLATION.md` - Installation and setup instructions
- Usage guides and how-to documents

**Do NOT place technical reports, implementation details, or development logs in the root directory.**

### Claude Reports Directory - Technical Documentation
All technical reports, implementation details, session summaries, and development documentation should be placed in:

```
Flamingo_Control/claude-reports/
```

**Naming Convention:**
All files in `claude-reports/` must follow this naming pattern:
```
YYYY-MM-DD-descriptive-name.md
```

Examples:
- `2024-11-05-position-display-fix.md`
- `2024-11-04-mvc-architecture-implementation.md`
- `2024-11-05-network-path-solution.md`

### What Goes in claude-reports/

**Include:**
- Implementation reports and technical summaries
- Bug fix documentation and root cause analysis
- Architecture decisions and design documentation
- Session summaries and work logs
- Integration verification reports
- Code refactoring summaries
- API documentation for internal components
- Development insights and lessons learned

**Do NOT Include:**
- User-facing installation guides
- Usage tutorials for end users
- Project README content
- Marketing or overview materials

## File Organization Rules

### Creating New Documentation

When creating any technical or development documentation:

1. **Always** place it in `claude-reports/`
2. **Always** include the date in the filename: `YYYY-MM-DD-`
3. Use lowercase with hyphens: `network-path-solution` not `Network_Path_Solution`
4. Be descriptive but concise in the filename

### Updating Existing Documentation

- User docs (README, INSTALLATION): Update in place in root
- Technical docs: Create a new dated file in `claude-reports/`
- Reference the previous report if updating/superseding it

## Example Structure

```
Flamingo_Control/
├── README.md                    # User-facing project overview
├── INSTALLATION.md              # User-facing setup guide
├── .claude/
│   └── claude.md               # This file
└── claude-reports/
    ├── 2024-11-04-mvc-refactor.md
    ├── 2024-11-05-position-fix.md
    ├── 2024-11-05-network-paths.md
    └── 2024-11-05-gui-improvements.md
```

## Why This Structure?

1. **Clean Root**: Users see only what they need without wading through development history
2. **Chronological**: Date-prefixed files naturally sort chronologically
3. **Discoverable**: All technical docs in one place (`claude-reports/`)
4. **Organized**: Clear separation between user docs and developer docs
5. **Maintainable**: Easy to archive or reference historical implementations

## Commit Guidelines

When committing documentation:
- Commits that add technical reports should include the report in `claude-reports/`
- Commits should reference the report file in the commit message
- Keep user-facing docs (README, INSTALLATION) up to date with actual functionality

---

**Last Updated:** 2024-11-05
**Maintained By:** Claude Code assistant
