# Security rules tests

`firestore.rules` and `storage.rules` against the Firebase emulators, with the
writes shaped exactly as `src/src/composables/useSubmissions.ts` makes them.
The rules are the only thing standing between an anonymous account and the
public check-in counter, the reviewer's queues and the private photo bucket,
so every rule that guards one of those has a case here that it must refuse.

CI runs this as the `firestore-rules` job. Locally it needs Java 21 on `PATH`
(the emulators' requirement):

```bash
cd terraform/bootstrap/rules/tests
pnpm install
pnpm test
```

Changing a rule still needs `terraform apply` in `terraform/bootstrap` to
reach production; these tests only say the file is right.
