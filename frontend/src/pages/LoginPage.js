import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { BarChart3, Eye, EyeOff, ArrowLeft } from 'lucide-react';

export default function LoginPage() {
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(identifier, password);
      navigate('/dashboard');
    } catch (err) {
      setError(err.response?.data?.detail || 'Invalid credentials');
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen flex bg-[#060D1A]" style={{ fontFamily: 'Manrope, sans-serif' }}>
      {/* Left branding */}
      <div className="hidden lg:flex lg:w-5/12 flex-col justify-between p-12 border-r border-white/5">
        <div>
          <Link to="/" className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors mb-12">
            <ArrowLeft size={16} />
            <span className="text-sm">Back to home</span>
          </Link>
          <div className="flex items-center gap-3 mb-12">
            <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center">
              <BarChart3 size={20} className="text-white" />
            </div>
            <span className="text-white font-bold text-xl">AgriBooks</span>
          </div>
          <h2 className="text-3xl font-extrabold text-white mb-3">Welcome back</h2>
          <p className="text-slate-400 text-sm leading-relaxed">
            Audit-Grade Retail Intelligence.
            <br />Serious control for serious businesses.
          </p>
        </div>
        <p className="text-slate-600 text-xs">
          Don't have an account?{' '}
          <Link to="/register" className="text-emerald-400 hover:underline">Start free trial</Link>
        </p>
      </div>

      {/* Right form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <Link to="/" className="flex lg:hidden items-center gap-2 text-slate-400 hover:text-white transition-colors mb-8">
            <ArrowLeft size={16} />
            <span className="text-sm">Back</span>
          </Link>
          <Card className="border-white/5 bg-white/[0.04] text-white shadow-2xl">
            <CardHeader className="text-center pb-4">
              <div className="w-12 h-12 rounded-xl bg-emerald-500 flex items-center justify-center mx-auto mb-3 lg:hidden">
                <BarChart3 size={22} className="text-white" />
              </div>
              <CardTitle className="text-xl text-white">Sign in to AgriBooks</CardTitle>
              <p className="text-sm text-slate-400 mt-1">Access your company dashboard</p>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                {error && (
                  <div data-testid="login-error" className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm px-4 py-3 rounded-lg">
                    {error}
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor="identifier" className="text-slate-300 text-sm">Email Address</Label>
                  <Input
                    id="identifier"
                    data-testid="login-username"
                    type="email"
                    value={identifier}
                    onChange={e => setIdentifier(e.target.value)}
                    placeholder="you@company.com"
                    className="bg-white/5 border-white/10 text-white placeholder:text-slate-600 h-11"
                    autoFocus
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password" className="text-slate-300 text-sm">Password</Label>
                  <div className="relative">
                    <Input
                      id="password"
                      data-testid="login-password"
                      type={showPw ? 'text' : 'password'}
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="Enter password"
                      className="bg-white/5 border-white/10 text-white placeholder:text-slate-600 h-11 pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPw(!showPw)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    >
                      {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>
                <div className="flex justify-end">
                  <Link to="/forgot-password" className="text-sm text-slate-500 hover:text-emerald-400 transition-colors">
                    Forgot password?
                  </Link>
                </div>
                <Button
                  type="submit"
                  data-testid="login-submit-btn"
                  disabled={loading}
                  className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl"
                >
                  {loading ? 'Signing in...' : 'Sign In'}
                </Button>
              </form>
              <div className="mt-5 text-center">
                <Link to="/register" className="text-emerald-400 hover:underline text-sm">
                  New company? Start free trial →
                </Link>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
