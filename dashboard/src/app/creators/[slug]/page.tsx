import CreatorForm from '@/components/CreatorForm';

export default function EditCreatorPage({ params }: { params: { slug: string } }) {
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-6 text-2xl font-bold">Edit Creator</h1>
      <CreatorForm slug={params.slug} />
    </div>
  );
}
