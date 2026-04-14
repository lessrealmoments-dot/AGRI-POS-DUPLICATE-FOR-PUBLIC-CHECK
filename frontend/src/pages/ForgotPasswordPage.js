import { useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { BarChart3, ArrowLeft, Mail, CheckCircle2 } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await axios.post(`${BACKEND_URL}/api/auth/forgot-password`, { email: email.trim().toLowerCase() });
      setSent(true);
    } catch (err) {
      setError(err.response?.data?.detail || 'Something went wrong. Please try again.');
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen flex bg-[#060D1A]" style={{ fontFamily: 'Manrope, sans-serif' }}>
      {/* Left branding */}
      <div className="hidden lg:flex lg:w-5/12 flex-col justify-between p-12 border-r border-white/5">
        <div>
          <Link to="/login" className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors mb-12">
            <ArrowLeft size={16} />
            <span className="text-sm">Back to sign in</span>
          </Link>
          <div className="flex items-center gap-3 mb-12">
            <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center">
              <BarChart3 size={20} className="text-white" />
            </div>
            <span className="text-white font-bold text-xl">AgriBooks</span>
          </div>
          <h2 className="text-3xl font-extrabold text-white mb-3">Forgot your password?</h2>
          <p className="text-slate-400 text-sm leading-relaxed">
            No worries — enter your email and we'll send you a reset link.
          </p>
        </div>
        <p className="text-slate-600 text-xs">
          Remembered it?{' '}
          <Link to="/login" className="text-emerald-400 hover:underline">Sign in</Link>
        </p>
      </div>

      {/* Right form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <Link to="/login" className="flex lg:hidden items-center gap-2 text-slate-400 hover:text-white transition-colors mb-8">
            <ArrowLeft size={16} />
            <span className="text-sm">Back to sign in</span>
          </Link>

          <Card className="border-white/5 bg-white/[0.04] text-white shadow-2xl">
            <CardHeader className="text-center pb-4">
              <div className="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto mb-3">
                <Mail size={22} className="text-emerald-400" />
              </div>
              <CardTitle className="text-xl text-white">Reset Password</CardTitle>
              <p className="text-sm text-slate-400 mt-1">
                Enter your account email to receive a reset link
              </p>
            </CardHeader>
            <CardContent>
              {sent ? (
                <div className="text-center py-4" data-testid="forgot-password-success">
                  <CheckCircle2 size={40} className="text-emerald-400 mx-auto mb-4" />
                  <p className="text-white font-semibold text-base mb-2">Check your inbox</p>
                  <p className="text-slate-400 text-sm leading-relaxed mb-6">
                    If <strong className="text-white">{email}</strong> is registered, we've sent a
                    password reset link. It expires in <strong className="text-white">1 hour</strong>.
                  </p>
                  <p className="text-slate-500 text-xs mb-4">
                    Didn't receive it? Check your spam folder, or{' '}
                    <button
                      onClick={() => { setSent(false); setError(''); }}
                      className="text-emerald-400 hover:underline"
                    >
                      try again
                    </button>
                    .
                  </p>
                  <Link to="/login">
                    <Button className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl">
                      Back to Sign In
                    </Button>
                  </Link>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="space-y-4">
                  {error && (
                    <div data-testid="forgot-password-error" className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm px-4 py-3 rounded-lg">
                      {error}
                    </div>
                  )}
                  <div className="space-y-1.5">
                    <Label htmlFor="email" className="text-slate-300 text-sm">Email Address</Label>
                    <Input
                      id="email"
                      data-testid="forgot-password-email"
                      type="email"
                      value={email}
                      onChange={e => setEmail(e.target.value)}
                      placeholder="you@company.com"
                      className="bg-white/5 border-white/10 text-white placeholder:text-slate-600 h-11"
                      required
                      autoFocus
                    />
                  </div>
                  <Button
                    type="submit"
                    data-testid="forgot-password-submit"
                    disabled={loading || !email.trim()}
                    className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl"
                  >
                    {loading ? 'Sending...' : 'Send Reset Link'}
                  </Button>
                  <div className="text-center">
                    <Link to="/login" className="text-slate-500 hover:text-slate-300 text-sm transition-colors">
                      Back to sign in
                    </Link>
                  </div>
                </form>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
